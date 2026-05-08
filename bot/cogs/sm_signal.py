"""Helius webhook 受信 + SM signal 通知 cog。

役割:
- bot プロセス内で aiohttp.web サーバを起動して Helius からの POST を受け取る
- 各 event を SM wallet 視点で分類 (bot/sm_signal_classifier.py)
- 連発抑制 / 群衆検出 / 大口判定を in-memory state で行い、 ラベル付与
- 指定された Discord スレッド (SM_SIGNAL_THREAD_ID) に Embed を投稿

ラベル:
    🟢 BUY        target を quote で買った
    🔴 SELL       target を quote で売った
    🐋 大口      |quote_change| が閾値以上 (SOL/stable で別閾値)
    🤝 群衆      別の SM wallet が同 mint を window 内に取引

連発 (同 wallet × 同 mint × 同 direction を window 内連続) は
SM_SIGNAL_DEDUP_WINDOW_MIN 内の 2 件目以降 suppress (bot 系の flood 対策)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import aiohttp.web as web
import discord
from discord.ext import commands

from bot.config import Config
from bot.sm_signal_classifier import (
    STABLE_LABELS,
    classify_swap,
    collect_involved_wallets,
    wallet_net_by_mint,
)
from bot.wallet_db import WalletDB

logger = logging.getLogger(__name__)

WALLET_CACHE_TTL_SEC = 60.0


class _SignalState:
    """signature dedup と (wallet, mint, direction) の rolling window 管理。"""

    def __init__(self, *, dedup_window_sec: int, group_window_sec: int):
        self._dedup_window = dedup_window_sec
        self._group_window = group_window_sec
        # 古い側から prune するため deque
        self._signatures: deque[tuple[float, str]] = deque()
        self._signatures_set: set[str] = set()
        self._obs: deque[tuple[float, str, str, str, str]] = deque()
        # (ts, wallet, mint, direction, signature)

    def is_duplicate_signature(self, sig: str) -> bool:
        return sig in self._signatures_set

    def record_signature(self, sig: str, ts: float) -> None:
        self._signatures.append((ts, sig))
        self._signatures_set.add(sig)
        self._prune(ts)

    def repeat_count(self, wallet: str, mint: str, direction: str, ts: float) -> int:
        cutoff = ts - self._dedup_window
        n = 0
        for o_ts, w, m, d, _ in self._obs:
            if o_ts < cutoff:
                continue
            if w == wallet and m == mint and d == direction:
                n += 1
        return n

    def other_wallets_for_mint(
        self, mint: str, direction: str, exclude_wallet: str, ts: float
    ) -> set[str]:
        cutoff = ts - self._group_window
        return {
            w
            for o_ts, w, m, d, _ in self._obs
            if o_ts >= cutoff and m == mint and d == direction and w != exclude_wallet
        }

    def record_observation(
        self, wallet: str, mint: str, direction: str, signature: str, ts: float
    ) -> None:
        self._obs.append((ts, wallet, mint, direction, signature))
        self._prune(ts)

    def _prune(self, now: float) -> None:
        cutoff = now - max(self._dedup_window, self._group_window)
        while self._signatures and self._signatures[0][0] < cutoff:
            _, old_sig = self._signatures.popleft()
            self._signatures_set.discard(old_sig)
        while self._obs and self._obs[0][0] < cutoff:
            self._obs.popleft()


class SmSignalCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config
        self._sm_wallets: set[str] = set()
        self._wallets_last_refresh: float = 0.0
        self._state = _SignalState(
            dedup_window_sec=config.sm_signal_dedup_window_min * 60,
            group_window_sec=config.sm_signal_group_window_min * 60,
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._setup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        # discord.py 2.0+ は __init__ から bot.loop に触れないので
        # async cog_load でサーバ起動を予約する。
        self._setup_task = asyncio.create_task(self._start_server())
        logger.info(
            "sm_signal: aiohttp サーバ起動を予約 (bind=%s:%d path=%s thread_id=%s)",
            self.config.webhook_bind_host, self.config.webhook_bind_port,
            self.config.webhook_path, self.config.sm_signal_thread_id,
        )

    async def cog_unload(self) -> None:
        if self._setup_task and not self._setup_task.done():
            self._setup_task.cancel()
        await self._stop_server()

    # ---- サーバ起動/停止 ----

    async def _start_server(self) -> None:
        try:
            await self.bot.wait_until_ready()
        except asyncio.CancelledError:
            return
        try:
            await self._refresh_wallets(force=True)
            app = web.Application()
            app.router.add_post(self.config.webhook_path, self._handle_webhook)
            app.router.add_get("/health", self._handle_health)
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            self._site = web.TCPSite(
                self._runner,
                host=self.config.webhook_bind_host,
                port=self.config.webhook_bind_port,
            )
            await self._site.start()
            logger.info(
                "[sm_signal] webhook server listening on %s:%d %s (sm wallets cached: %d)",
                self.config.webhook_bind_host, self.config.webhook_bind_port,
                self.config.webhook_path, len(self._sm_wallets),
            )
        except Exception:
            logger.exception("[sm_signal] サーバ起動失敗")

    async def _stop_server(self) -> None:
        try:
            if self._site is not None:
                await self._site.stop()
                self._site = None
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None
            logger.info("[sm_signal] webhook server stopped")
        except Exception:
            logger.exception("[sm_signal] サーバ停止失敗")

    # ---- handlers ----

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "sm_wallets": len(self._sm_wallets)})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        # Authorization header check (Helius が authHeader で送ってきた値と一致するか)
        if self.config.helius_webhook_auth_header:
            got = request.headers.get("Authorization") or ""
            if got != self.config.helius_webhook_auth_header:
                logger.warning(
                    "[sm_signal] Authorization mismatch from %s",
                    request.remote,
                )
                return web.Response(status=401, text="unauthorized")

        try:
            data = await request.json()
        except Exception:
            logger.warning("[sm_signal] non-JSON body from %s", request.remote)
            return web.Response(status=400, text="bad json")

        events = data if isinstance(data, list) else (data.get("events") or [] if isinstance(data, dict) else [])
        if not isinstance(events, list):
            return web.Response(status=400, text="bad shape")

        await self._refresh_wallets()

        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                await self._process_event(event)
            except Exception:
                logger.exception("[sm_signal] event 処理失敗 sig=%s", event.get("signature"))

        return web.Response(status=200, text="ok")

    # ---- 内部処理 ----

    async def _refresh_wallets(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._wallets_last_refresh) < WALLET_CACHE_TTL_SEC:
            return
        try:
            async with WalletDB() as db:
                wallets = await db.list_all_sm_wallets()
            self._sm_wallets = set(wallets)
            self._wallets_last_refresh = now
            logger.debug("[sm_signal] sm wallets refreshed: %d", len(self._sm_wallets))
        except Exception:
            logger.exception("[sm_signal] sm wallets refresh 失敗 (cache 維持)")

    async def _process_event(self, event: dict[str, Any]) -> None:
        sig = event.get("signature")
        ts = event.get("timestamp")
        if not isinstance(sig, str) or not sig:
            return
        if not isinstance(ts, (int, float)):
            return
        ts_f = float(ts)

        if self._state.is_duplicate_signature(sig):
            logger.debug("[sm_signal] dup signature skip: %s", sig)
            return
        self._state.record_signature(sig, ts_f)

        involved = collect_involved_wallets(event)
        sm_involved = involved & self._sm_wallets
        if not sm_involved:
            return

        for wallet in sm_involved:
            net = wallet_net_by_mint(event, wallet)
            cls = classify_swap(net)
            if not cls:
                logger.debug(
                    "[sm_signal] classify skip wallet=%s sig=%s (net=%s)",
                    wallet, sig, net,
                )
                continue

            direction = cls["direction"]
            target_mint = cls["target_mint"]
            quote_label = cls["quote_label"]
            quote_change = cls["quote_change"]
            is_large = self._is_large(quote_label, quote_change)

            # 連発判定 (notify gate のみ。 DB 蓄積は repeat でも残す = 集計の母数になる)
            prior = self._state.repeat_count(wallet, target_mint, direction, ts_f)
            is_repeat = prior > 0

            # 1) DB 蓄積 (sm_summary cog の入力)
            await self._record_event_to_db(
                sig=sig, ts=int(ts_f), wallet=wallet, cls=cls,
                is_large=is_large, is_suppressed=is_repeat,
            )

            # 2) Discord 通知判定
            if is_repeat:
                logger.info(
                    "[sm_signal] suppress repeat: wallet=%s mint=%s dir=%s prior=%d",
                    wallet, target_mint, direction, prior,
                )
                self._state.record_observation(wallet, target_mint, direction, sig, ts_f)
                continue
            if direction == "SELL" and not self.config.sm_signal_include_sell:
                self._state.record_observation(wallet, target_mint, direction, sig, ts_f)
                continue

            others = self._state.other_wallets_for_mint(
                target_mint, direction, wallet, ts_f
            )

            await self._post_signal(
                event=event,
                wallet=wallet,
                cls=cls,
                others=others,
                is_large=is_large,
            )
            self._state.record_observation(wallet, target_mint, direction, sig, ts_f)

    async def _record_event_to_db(
        self,
        *,
        sig: str,
        ts: int,
        wallet: str,
        cls: dict[str, Any],
        is_large: bool,
        is_suppressed: bool,
    ) -> None:
        try:
            async with WalletDB() as db:
                await db.insert_sm_signal_event(
                    signature=sig,
                    block_ts=ts,
                    wallet=wallet,
                    target_mint=cls["target_mint"],
                    target_change=cls["target_change"],
                    quote_label=cls["quote_label"],
                    quote_mint=cls.get("quote_mint"),
                    quote_change=cls["quote_change"],
                    direction=cls["direction"],
                    is_large=is_large,
                    is_suppressed=is_suppressed,
                )
        except Exception:
            logger.exception("[sm_signal] DB 記録失敗 sig=%s wallet=%s", sig, wallet)

    def _is_large(self, quote_label: str, quote_change: float) -> bool:
        v = abs(quote_change)
        if quote_label == "SOL":
            return v >= self.config.sm_signal_large_sol_min
        if quote_label in STABLE_LABELS.values():
            return v >= self.config.sm_signal_large_stable_min
        return False

    async def _post_signal(
        self,
        *,
        event: dict[str, Any],
        wallet: str,
        cls: dict[str, Any],
        others: set[str],
        is_large: bool,
    ) -> None:
        thread_id = self.config.sm_signal_thread_id
        if not thread_id:
            logger.warning("[sm_signal] SM_SIGNAL_THREAD_ID 未設定 → 通知スキップ")
            return
        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except Exception:
                logger.exception("[sm_signal] thread fetch 失敗 id=%s", thread_id)
                return
        if not isinstance(thread, (discord.Thread, discord.TextChannel)):
            logger.warning(
                "[sm_signal] SM_SIGNAL_THREAD_ID=%s は Thread / TextChannel ではない (%s)",
                thread_id, type(thread).__name__,
            )
            return

        embed = _build_signal_embed(
            event=event,
            wallet=wallet,
            cls=cls,
            others=others,
            is_large=is_large,
        )
        try:
            await thread.send(embed=embed)
        except Exception:
            logger.exception("[sm_signal] 通知投稿失敗")


# ---- formatting helpers ----


def _short(addr: str | None) -> str:
    if not isinstance(addr, str) or not addr:
        return "-"
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}…{addr[-4:]}"


def _fmt_amount(v: float) -> str:
    a = abs(v)
    if a >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{v/1_000:.2f}K"
    if a >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


def _build_signal_embed(
    *,
    event: dict[str, Any],
    wallet: str,
    cls: dict[str, Any],
    others: set[str],
    is_large: bool,
) -> discord.Embed:
    direction = cls["direction"]
    target_mint = cls["target_mint"]
    target_change = cls["target_change"]
    quote_label = cls["quote_label"]
    quote_change = cls["quote_change"]

    is_buy = direction == "BUY"
    color = 0x4CAF50 if is_buy else 0xE53935
    dir_emoji = "🟢" if is_buy else "🔴"
    dir_word = "BUY" if is_buy else "SELL"

    labels: list[str] = [f"{dir_emoji} {dir_word}"]
    if is_large:
        labels.append("🐋 大口")
    if others:
        labels.append(f"🤝 群衆×{len(others)}")
    label_str = " ".join(labels)

    title = f"{label_str} {_short(wallet)}"
    embed = discord.Embed(title=title, color=color)

    if is_buy:
        flow_text = (
            f"**{_fmt_amount(abs(quote_change))} {quote_label}** → "
            f"**{_fmt_amount(abs(target_change))} token**"
        )
    else:
        flow_text = (
            f"**{_fmt_amount(abs(target_change))} token** → "
            f"**{_fmt_amount(abs(quote_change))} {quote_label}**"
        )
    embed.add_field(name="💱 取引", value=flow_text, inline=False)

    nansen_url = f"https://app.nansen.ai/profiler/{wallet}?chain=solana"
    solscan_wallet = f"https://solscan.io/account/{wallet}"
    embed.add_field(
        name="👛 wallet",
        value=f"`{_short(wallet)}` · [solscan]({solscan_wallet}) · [Nansen]({nansen_url})",
        inline=False,
    )

    dexscreener = f"https://dexscreener.com/solana/{target_mint}"
    solscan_token = f"https://solscan.io/token/{target_mint}"
    embed.add_field(
        name="🪙 token",
        value=f"`{_short(target_mint)}` · [DexScreener]({dexscreener}) · [solscan]({solscan_token})",
        inline=False,
    )

    if others:
        sample = ", ".join(_short(w) for w in list(others)[:5])
        more = f" ほか {len(others)-5}" if len(others) > 5 else ""
        embed.add_field(
            name=f"🤝 同 mint を直近 window 内に取引した別 SM ({len(others)})",
            value=f"{sample}{more}",
            inline=False,
        )

    sig = event.get("signature") or ""
    if sig:
        sig_url = f"https://solscan.io/tx/{sig}"
        embed.add_field(
            name="🔗 tx",
            value=f"[`{sig[:12]}…`]({sig_url})",
            inline=True,
        )

    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        try:
            embed.timestamp = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass

    return embed


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(SmSignalCog(bot, config))
