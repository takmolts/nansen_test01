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
from bot.links import research_links_md, trade_links_md, x_search_links_md
from bot.sm_signal_classifier import (
    STABLE_LABELS,
    classify_swap,
    collect_involved_wallets,
    wallet_net_by_mint,
)
from bot.token_info import TokenInfo, get_token_info
from bot.views import build_rating_view
from bot.wallet_db import WalletDB

logger = logging.getLogger(__name__)

WALLET_CACHE_TTL_SEC = 60.0


class _SignalState:
    """signature dedup と (wallet, mint, direction) の rolling window 管理。"""

    def __init__(
        self,
        *,
        dedup_window_sec: int,
        group_window_sec: int,
        realtime_window_sec: int = 0,
    ):
        self._dedup_window = dedup_window_sec
        self._group_window = group_window_sec
        self._realtime_window = realtime_window_sec
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

    def distinct_wallets_for_mint(
        self, mint: str, direction: str, window_sec: int, ts: float
    ) -> set[str]:
        """直近 window 内で同 mint × 同 direction を取引した wallet 一覧 (自身含む)。"""
        cutoff = ts - window_sec
        return {
            w
            for o_ts, w, m, d, _ in self._obs
            if o_ts >= cutoff and m == mint and d == direction
        }

    def _prune(self, now: float) -> None:
        cutoff = now - max(self._dedup_window, self._group_window, self._realtime_window)
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
        self._sm_wallet_labels: dict[str, str] = {}
        self._sm_wallet_ratings: dict[str, int] = {}
        self._wallets_last_refresh: float = 0.0
        self._state = _SignalState(
            dedup_window_sec=config.sm_signal_dedup_window_min * 60,
            group_window_sec=config.sm_signal_group_window_min * 60,
            realtime_window_sec=config.sm_summary_realtime_window_min * 60,
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
        logger.info(
            "[sm_signal] received %d events from %s (sm wallets cached: %d)",
            len(events), request.remote, len(self._sm_wallets),
        )

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
                labels = await db.get_sm_wallet_labels()
                ratings = await db.get_wallet_ratings()
            self._sm_wallets = set(wallets)
            self._sm_wallet_labels = labels
            self._sm_wallet_ratings = ratings
            self._wallets_last_refresh = now
            logger.debug(
                "[sm_signal] sm wallets refreshed: %d (labels=%d)",
                len(self._sm_wallets), len(self._sm_wallet_labels),
            )
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
            logger.debug(
                "[sm_signal] no SM match: sig=%s involved=%d", sig, len(involved),
            )
            return
        logger.info(
            "[sm_signal] SM match sig=%s wallets=%d",
            sig, len(sm_involved),
        )

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

            logger.info(
                "[sm_signal] %s wallet=%s mint=%s %s%.4f%s large=%s others=%d",
                direction, wallet[:8] + "…",
                target_mint[:8] + "…",
                "+" if cls["target_change"] > 0 else "",
                cls["target_change"], "",
                is_large, len(others),
            )
            await self._post_signal(
                event=event,
                wallet=wallet,
                cls=cls,
                others=others,
                is_large=is_large,
            )
            self._state.record_observation(wallet, target_mint, direction, sig, ts_f)

            # 速報 (注目銘柄リアルタイム通知 → SM_SUMMARY_CHANNEL_ID)
            if direction == "BUY" and self.config.sm_summary_realtime_enabled:
                await self._maybe_post_realtime(
                    event=event, wallet=wallet, cls=cls, ts_f=ts_f,
                )

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

    def _is_realtime_whale_buy(self, quote_label: str, quote_change: float) -> bool:
        v = abs(quote_change)
        if quote_label == "SOL":
            return v >= self.config.sm_summary_realtime_whale_sol_min
        if quote_label in STABLE_LABELS.values():
            return v >= self.config.sm_summary_realtime_whale_stable_min
        return False

    async def _maybe_post_realtime(
        self,
        *,
        event: dict[str, Any],
        wallet: str,
        cls: dict[str, Any],
        ts_f: float,
    ) -> None:
        """BUY が群衆ブレイク or whale 単発を満たせば sm_summary に速報を依頼。"""
        target_mint = cls["target_mint"]
        quote_label = cls["quote_label"]
        quote_change = cls["quote_change"]

        is_whale_buy = self._is_realtime_whale_buy(quote_label, quote_change)
        window_sec = self.config.sm_summary_realtime_window_min * 60
        buyers = self._state.distinct_wallets_for_mint(
            target_mint, "BUY", window_sec, ts_f
        )
        # record_observation 済みなので自身は含まれているはずだが、 念のため。
        buyers.add(wallet)
        is_crowd_break = len(buyers) >= self.config.sm_summary_realtime_min_buyers

        if not (is_whale_buy or is_crowd_break):
            return

        cog = self.bot.get_cog("SmSummaryCog")
        if cog is None:
            logger.debug("[sm_signal] sm_summary cog 未ロード → 速報スキップ")
            return
        notify = getattr(cog, "notify_realtime", None)
        if not callable(notify):
            logger.debug("[sm_signal] sm_summary cog に notify_realtime 無し")
            return

        other_wallets = buyers - {wallet}
        other_labels = {w: self._sm_wallet_labels.get(w) for w in other_wallets}
        try:
            await notify(
                event=event,
                wallet=wallet,
                cls=cls,
                distinct_buyers=len(buyers),
                other_wallets=other_wallets,
                other_wallets_labels=other_labels,
                wallet_label=self._sm_wallet_labels.get(wallet),
                is_whale_buy=is_whale_buy,
                is_crowd_break=is_crowd_break,
            )
        except Exception:
            logger.exception("[sm_signal] 速報通知失敗 mint=%s", target_mint)

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

        # 投稿前に DexScreener から symbol / mcap を取得 (TTL cache 付き)
        token_info: TokenInfo | None = None
        try:
            token_info = await get_token_info(cls["target_mint"])
        except Exception:
            logger.warning(
                "[sm_signal] token_info 取得失敗 mint=%s",
                cls["target_mint"], exc_info=True,
            )

        wallet_label = self._sm_wallet_labels.get(wallet)
        others_labels = {w: self._sm_wallet_labels.get(w) for w in others}
        wallet_rating = self._sm_wallet_ratings.get(wallet)
        others_ratings = {w: self._sm_wallet_ratings.get(w) for w in others}

        embed = _build_signal_embed(
            event=event,
            wallet=wallet,
            cls=cls,
            others=others,
            is_large=is_large,
            token_info=token_info,
            wallet_label=wallet_label,
            others_labels=others_labels,
            wallet_rating=wallet_rating,
            others_ratings=others_ratings,
        )
        try:
            await thread.send(embed=embed, view=build_rating_view(wallet))
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


def _fmt_usd(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a/1_000:.2f}K"
    return f"{sign}${a:.0f}"


def _stars(rating: int | None) -> str:
    """rating(1-5) を ⭐ 文字列に。 None/0 は空文字。"""
    try:
        n = int(rating) if rating else 0
    except (TypeError, ValueError):
        n = 0
    return "⭐" * n


def _build_signal_embed(
    *,
    event: dict[str, Any],
    wallet: str,
    cls: dict[str, Any],
    others: set[str],
    is_large: bool,
    token_info: TokenInfo | None = None,
    wallet_label: str | None = None,
    others_labels: dict[str, str | None] | None = None,
    wallet_rating: int | None = None,
    others_ratings: dict[str, int | None] | None = None,
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

    sym = token_info.symbol if token_info and token_info.symbol else None

    # タイトルは方向と wallet のみ (ticker / ラベルは要素に分離)
    wallet_disp = _short(wallet)
    if wallet_label:
        wallet_disp = f"{wallet_disp} ({wallet_label})"
    rating_prefix = f"{_stars(wallet_rating)} " if wallet_rating else ""
    title = f"{dir_emoji} {dir_word}  ·  {rating_prefix}{wallet_disp}"

    embed = discord.Embed(title=title, color=color)
    if token_info and token_info.image_url:
        embed.set_thumbnail(url=token_info.image_url)

    # description: 1 行 1 メタ で縦に詰める
    token_label = f"${sym}" if sym else "token"
    if is_buy:
        flow_text = (
            f"{_fmt_amount(abs(quote_change))} {quote_label} → "
            f"{_fmt_amount(abs(target_change))} {token_label}"
        )
    else:
        flow_text = (
            f"{_fmt_amount(abs(target_change))} {token_label} → "
            f"{_fmt_amount(abs(quote_change))} {quote_label}"
        )

    nansen_url = f"https://app.nansen.ai/profiler/{wallet}?chain=solana"
    solscan_wallet = f"https://solscan.io/account/{wallet}"
    wallet_line = f"`{_short(wallet)}`"
    if wallet_rating:
        wallet_line += f" {_stars(wallet_rating)}"
    if wallet_label:
        wallet_line += f" **{wallet_label}**"
    wallet_line += f" · [solscan]({solscan_wallet}) · [Nansen]({nansen_url})"

    desc_lines: list[str] = []
    if sym:
        desc_lines.append(f"🪙 ticker：**${sym}**")
    desc_lines.append(f"♻️ 取引：**{flow_text}**")
    if token_info and token_info.market_cap:
        desc_lines.append(f"📈 mcap：{_fmt_usd(token_info.market_cap)}")
    desc_lines.append(f"💬 CA：`{target_mint}`")
    x_md = x_search_links_md(sym, target_mint)
    if x_md:
        desc_lines.append(f"🔍 X：{x_md}")
    research_md = research_links_md(sym, target_mint)
    if research_md:
        desc_lines.append(f"🔎 {research_md}")
    desc_lines.append(f"🔗 Trade：{trade_links_md(target_mint, chain='solana')}")
    desc_lines.append(f"👛 wallet：{wallet_line}")
    sig = event.get("signature") or ""
    if sig:
        sig_url = f"https://solscan.io/tx/{sig}"
        desc_lines.append(f"📌 tx：[`{sig[:12]}…`]({sig_url})")
    embed.description = "\n".join(desc_lines)

    # ラベル (大口 / 群衆) は専用フィールド (1 行で詰めるが視覚的に区切る)
    extra_labels: list[str] = []
    if is_large:
        extra_labels.append("🐋 大口")
    if others:
        extra_labels.append(f"🤝 群衆×{len(others)}")
    if extra_labels:
        embed.add_field(name="📗 ラベル", value=" ".join(extra_labels), inline=False)

    # 群衆メンバー詳細 (label 付きで表示)。 名前数が多くなるので field のまま
    if others:
        ol = others_labels or {}
        orr = others_ratings or {}
        items: list[str] = []
        for w in list(others)[:5]:
            short = _short(w)
            lbl = ol.get(w)
            star = _stars(orr.get(w))
            disp = f"{short} ({lbl})" if lbl else short
            items.append(f"{star} {disp}" if star else disp)
        sample = ", ".join(items)
        more = f" ほか {len(others)-5}" if len(others) > 5 else ""
        embed.add_field(
            name=f"🤝 同 mint を直近 window 内に取引した別 SM ({len(others)})",
            value=f"{sample}{more}",
            inline=False,
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
