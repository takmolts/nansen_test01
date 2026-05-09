"""sm_signal_events を毎時集計し、 群衆性の高い銘柄を Embed リストで通知する cog。

役割:
- 蓄積された SM SWAP events から「過去 SUMMARY_WINDOW_MIN 内に N 人以上の SM が
  BUY した mint」 を抽出
- スコア付けして TOP_N をランキング
- DIGEST_CHANNEL_ID (or SM_SUMMARY_CHANNEL_ID 上書き) に Embed 投稿
- Nansen クレジットは消費しない (DexScreener のみ symbol 解決に使用)

スコア式:
    score = distinct_buyers * 10
          + buy_trades      * 0.5
          + n_large_buys    * 5
          + log10(1 + sum_buy_value_usd_proxy)
    ※ sum_buy_value_usd_proxy = sum_buy_sol * 200 + sum_buy_stable

毎時 0 分に loop 起動。 手動は /sm-summary。
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.check import AnalyzeButtonView
from bot.config import Config
from bot.links import grok_token_link_md, trade_links_md, x_search_links_md
from bot.token_info import TokenInfo, get_token_info, get_token_infos
from bot.wallet_db import WalletDB

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
SOL_USD_PROXY = 200.0  # スコア計算と sum_value_proxy のための粗推定価格


def _short(addr: str | None) -> str:
    if not isinstance(addr, str) or not addr:
        return "-"
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}…{addr[-4:]}"


def _fmt_usd(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a/1_000:.2f}K"
    return f"{sign}${a:.0f}"


def _fmt_sol(v: float) -> str:
    a = abs(v)
    if a >= 1_000:
        return f"{a/1_000:.2f}K"
    if a >= 1:
        return f"{a:.2f}"
    return f"{a:.3f}"


def _score(row: dict) -> float:
    distinct_buyers = int(row.get("distinct_buyers") or 0)
    buy_trades = int(row.get("buy_trades") or 0)
    n_large_buys = int(row.get("n_large_buys") or 0)
    sum_buy_sol = float(row.get("sum_buy_sol") or 0)
    sum_buy_stable = float(row.get("sum_buy_stable") or 0)
    sum_value_proxy = sum_buy_sol * SOL_USD_PROXY + sum_buy_stable
    return (
        distinct_buyers * 10
        + buy_trades * 0.5
        + n_large_buys * 5
        + math.log10(1.0 + sum_value_proxy)
    )


class SmSummaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config
        self._auto_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # 速報の mint 単位 cooldown (mint -> last_notify_ts)
        self._realtime_cooldown: dict[str, float] = {}

    async def cog_load(self) -> None:
        # discord.py 2.0+ では __init__ から bot.loop に触れないため、
        # async hook の cog_load でタスク生成。
        if self.config.sm_summary_enabled:
            self._auto_task = asyncio.create_task(self._hourly_loop())
            logger.info(
                "sm_summary auto-loop 起動 (window=%d 分 / min_wallets=%d / top=%d / channel=%s)",
                self.config.sm_summary_window_min, self.config.sm_summary_min_wallets,
                self.config.sm_summary_top_n, self.config.sm_summary_channel_id,
            )
        else:
            logger.info("sm_summary auto-loop は DISABLED (SM_SUMMARY_ENABLED=false)")

    def cog_unload(self) -> None:
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()

    # ---- 毎時 loop (毎正時 0 分に発火) ----

    async def _hourly_loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
        except asyncio.CancelledError:
            return
        while not self.bot.is_closed():
            now = datetime.now(JST)
            next_run = (now + timedelta(hours=1)).replace(
                minute=0, second=0, microsecond=0
            )
            sleep_sec = (next_run - now).total_seconds()
            logger.info(
                "[sm_summary] 次回集計まで %.0f 秒待機 (target=%s)",
                sleep_sec, next_run.isoformat(timespec="seconds"),
            )
            try:
                await asyncio.sleep(sleep_sec)
            except asyncio.CancelledError:
                return
            try:
                await self._run_summary(tag="auto")
            except Exception:
                logger.exception("[sm_summary] 集計失敗")

    # ---- 集計本体 ----

    async def _run_summary(self, *, tag: str) -> dict[str, Any] | None:
        async with self._lock:
            window_min = self.config.sm_summary_window_min
            now_ts = int(time.time())
            since_ts = now_ts - window_min * 60

            async with WalletDB() as db:
                rows = await db.aggregate_sm_signals(
                    since_block_ts=since_ts,
                    min_distinct_buyers=self.config.sm_summary_min_wallets,
                    limit=self.config.sm_summary_top_n,
                )
                total_events_in_window = await db.sm_signal_events_count(
                    since_block_ts=since_ts
                )

            if not rows:
                logger.info(
                    "[sm_summary:%s] 該当 mint なし (window=%d 分, total events=%d)",
                    tag, window_min, total_events_in_window,
                )
                return {
                    "tag": tag,
                    "rows": [],
                    "window_min": window_min,
                    "total_events": total_events_in_window,
                }

            # dict 化 + score 計算 + buyers list 取得
            enriched: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["score"] = _score(d)
                async with WalletDB() as db:
                    buyer_rows = await db.list_buyers_for_mint(
                        target_mint=d["target_mint"], since_block_ts=since_ts
                    )
                d["buyers"] = [dict(b) for b in buyer_rows]
                enriched.append(d)

            # symbol / market cap を DexScreener で補完 (TOP_N のみ)
            await self._enrich_with_dexscreener(enriched)

            # 投稿
            await self._post_summary(enriched, tag=tag, window_min=window_min)
            return {
                "tag": tag,
                "rows": enriched,
                "window_min": window_min,
                "total_events": total_events_in_window,
            }

    async def _enrich_with_dexscreener(self, rows: list[dict]) -> None:
        """共有 token_info キャッシュ経由で symbol / mcap / price を補完。"""
        if not rows:
            return
        try:
            addrs = [r.get("target_mint") for r in rows if isinstance(r.get("target_mint"), str)]
            infos = await get_token_infos(addrs)
            for r in rows:
                addr = r.get("target_mint")
                if not isinstance(addr, str):
                    continue
                info = infos.get(addr)
                if info is None:
                    continue
                if info.symbol:
                    r["dex_symbol"] = info.symbol
                if info.market_cap is not None:
                    r["dex_marketcap"] = info.market_cap
                if info.price_usd is not None:
                    r["dex_price_usd"] = info.price_usd
                if info.image_url:
                    r["dex_image_url"] = info.image_url
        except Exception:
            logger.exception("[sm_summary] token_info 補完失敗 (継続)")

    async def _post_summary(
        self, rows: list[dict], *, tag: str, window_min: int
    ) -> None:
        # Smart Wallet Signal Summary の集計通知は一旦無効化 (速報のみ運用)
        logger.info(
            "[sm_summary:%s] 集計通知は無効化中のため送信スキップ (rows=%d, window=%d 分)",
            tag, len(rows), window_min,
        )
        return

    # ---- 速報 (sm_signal cog から呼ばれる) ----

    async def notify_realtime(
        self,
        *,
        event: dict[str, Any],
        wallet: str,
        cls: dict[str, Any],
        distinct_buyers: int,
        other_wallets: set[str],
        other_wallets_labels: dict[str, str | None],
        wallet_label: str | None,
        is_whale_buy: bool,
        is_crowd_break: bool,
    ) -> None:
        """sm_signal cog の BUY hook から呼ばれる。 mint cooldown を見て speed 投稿する。"""
        if not self.config.sm_summary_realtime_enabled:
            return
        target_mint = cls.get("target_mint")
        if not isinstance(target_mint, str) or not target_mint:
            return

        cooldown_sec = self.config.sm_summary_realtime_cooldown_min * 60
        now = time.time()
        last = self._realtime_cooldown.get(target_mint, 0.0)
        if now - last < cooldown_sec:
            logger.info(
                "[sm_summary:realtime] cooldown skip mint=%s elapsed=%.0fs",
                target_mint, now - last,
            )
            return

        ch_id = self.config.sm_summary_channel_id
        if not ch_id:
            logger.warning("[sm_summary:realtime] 投稿先未設定 → 速報スキップ")
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except Exception:
                logger.exception(
                    "[sm_summary:realtime] channel fetch 失敗 id=%s", ch_id
                )
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "[sm_summary:realtime] channel %s が TextChannel/Thread ではない (%s)",
                ch_id, type(channel).__name__,
            )
            return

        token_info: TokenInfo | None = None
        try:
            token_info = await get_token_info(target_mint)
        except Exception:
            logger.warning(
                "[sm_summary:realtime] token_info 取得失敗 mint=%s",
                target_mint, exc_info=True,
            )

        embed = _build_realtime_embed(
            event=event,
            wallet=wallet,
            cls=cls,
            distinct_buyers=distinct_buyers,
            other_wallets=other_wallets,
            other_wallets_labels=other_wallets_labels,
            wallet_label=wallet_label,
            is_whale_buy=is_whale_buy,
            is_crowd_break=is_crowd_break,
            window_min=self.config.sm_summary_realtime_window_min,
            min_buyers=self.config.sm_summary_realtime_min_buyers,
            token_info=token_info,
        )

        triggers: list[str] = []
        if is_whale_buy:
            triggers.append("whale_buy")
        if is_crowd_break:
            triggers.append(f"crowd_break(N={distinct_buyers})")
        logger.info(
            "[sm_summary:realtime] post mint=%s wallet=%s triggers=%s",
            target_mint, wallet[:8] + "…", ",".join(triggers) or "?",
        )
        view = AnalyzeButtonView(ca=target_mint, config=self.config)
        try:
            await channel.send(embed=embed, view=view)
            self._realtime_cooldown[target_mint] = now
        except Exception:
            logger.exception(
                "[sm_summary:realtime] 投稿失敗 mint=%s", target_mint
            )

    # ---- Slash command ----

    @app_commands.command(
        name="sm-summary",
        description="今すぐ Smart Money 銘柄の集計サマリを実行します",
    )
    async def sm_summary_cmd(self, interaction: discord.Interaction):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        try:
            result = await self._run_summary(tag="manual")
        except Exception:
            logger.exception("/sm-summary 失敗")
            await interaction.followup.send(
                "想定外のエラーが発生しました。 ログを確認してください。",
                ephemeral=True,
            )
            return
        if not result or not result.get("rows"):
            await interaction.followup.send(
                f"該当する mint がありません (window={result['window_min'] if result else '?'} 分 / total events={result['total_events'] if result else 0})。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "集計通知は現在無効化されているため Discord には投稿していません (速報のみ運用中)。",
            ephemeral=True,
        )


def _build_header_text(
    rows: list[dict], *, window_min: int, tag: str
) -> str:
    """Discord メッセージ本文 (embeds の上に出るテキスト) を生成。"""
    suffix = "(自動)" if tag == "auto" else "(手動)"
    return (
        f"🛰️ **Smart Wallet Signal Summary** {suffix} 直近 {window_min} 分\n"
        f"群衆 ≥ 2 の銘柄 **{len(rows)}** 件 ｜ "
        f"スコア = 群衆×10 + 取引数×0.5 + 大口×5 + log10(USD proxy)"
    )


def _build_token_embed(r: dict, *, rank: int) -> discord.Embed:
    addr = r.get("target_mint", "")
    sym = r.get("dex_symbol")
    score = float(r.get("score") or 0.0)
    n_buyers = int(r.get("distinct_buyers") or 0)
    n_sellers = int(r.get("distinct_sellers") or 0)
    sell_trades = int(r.get("sell_trades") or 0)
    n_large = int(r.get("n_large_buys") or 0)
    sum_sol = float(r.get("sum_buy_sol") or 0)
    sum_stable = float(r.get("sum_buy_stable") or 0)
    sum_value_usd = sum_sol * SOL_USD_PROXY + sum_stable
    mcap = r.get("dex_marketcap")
    img = r.get("dex_image_url")
    first_ts = r.get("first_seen_ts")
    last_ts = r.get("last_seen_ts")
    buyers = r.get("buyers") or []

    head_token = f"${sym}" if sym else _short(addr)
    title = f"#{rank}  {head_token}"

    embed = discord.Embed(title=title, color=0xE91E63)
    if isinstance(img, str) and img.startswith("http"):
        embed.set_thumbnail(url=img)

    # description: 1 行 1 メタで縦圧縮
    desc_lines: list[str] = []
    desc_lines.append(f"🏆 score：**{score:.1f}**")
    if first_ts and last_ts:
        try:
            first_dt = datetime.fromtimestamp(int(first_ts), tz=JST)
            last_dt = datetime.fromtimestamp(int(last_ts), tz=JST)
            desc_lines.append(
                f"🕒 期間：{first_dt.strftime('%H:%M')}〜{last_dt.strftime('%H:%M')}"
            )
        except Exception:
            pass
    if sym:
        desc_lines.append(f"🪙 ticker：**${sym}**")
    desc_lines.append(
        f"💵 SM buy：**{_fmt_usd(sum_value_usd)}** "
        f"({_fmt_sol(sum_sol)} SOL + {_fmt_usd(sum_stable)} stable)"
    )
    traders_summary = f"👥 traders：BUY **{n_buyers}**"
    if n_sellers or sell_trades:
        traders_summary += f"  /  SELL {n_sellers}w·{sell_trades}t"
    if n_large:
        traders_summary += f"  /  🐋×{n_large}"
    desc_lines.append(traders_summary)
    if mcap:
        desc_lines.append(f"📈 mcap：{_fmt_usd(float(mcap))}")
    if addr:
        desc_lines.append(f"💬 CA：`{addr}`")
        x_md = x_search_links_md(sym, addr)
        if x_md:
            desc_lines.append(f"🔍 X：{x_md}")
        desc_lines.append(f"🤖 {grok_token_link_md(sym, addr)}")
        desc_lines.append(f"🔗 Trade：{trade_links_md(addr, chain='solana')}")
    embed.description = "\n".join(desc_lines)

    # buyers 詳細 (label 付き、 名前数があるので field 維持)
    if buyers:
        def _disp(b: dict) -> str:
            w = b.get("wallet") or ""
            short = _short(w)
            lbl = b.get("label")
            return f"{short} ({lbl})" if isinstance(lbl, str) and lbl else short

        sample = ", ".join(_disp(b) for b in buyers[:5])
        more = f" +{len(buyers)-5}" if len(buyers) > 5 else ""
        embed.add_field(
            name=f"🤝 buyers ({len(buyers)})",
            value=sample + more,
            inline=False,
        )

    return embed


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


def _build_realtime_embed(
    *,
    event: dict[str, Any],
    wallet: str,
    cls: dict[str, Any],
    distinct_buyers: int,
    other_wallets: set[str],
    other_wallets_labels: dict[str, str | None],
    wallet_label: str | None,
    is_whale_buy: bool,
    is_crowd_break: bool,
    window_min: int,
    min_buyers: int,
    token_info: TokenInfo | None,
) -> discord.Embed:
    target_mint = cls["target_mint"]
    target_change = cls["target_change"]
    quote_label = cls["quote_label"]
    quote_change = cls["quote_change"]

    sym = token_info.symbol if token_info and token_info.symbol else None
    token_label = f"${sym}" if sym else "token"
    head = f"${sym}" if sym else _short(target_mint)

    badges: list[str] = []
    if is_whale_buy:
        badges.append("🐋 大口")
    if is_crowd_break:
        badges.append(f"🤝 群衆 {distinct_buyers}/{min_buyers}")
    badge_text = "  ".join(badges) if badges else ""

    title = f"🚨 速報 BUY  ·  {head}"
    embed = discord.Embed(title=title, color=0xFF9800)
    if token_info and token_info.image_url:
        embed.set_thumbnail(url=token_info.image_url)

    flow_text = (
        f"{_fmt_amount(abs(quote_change))} {quote_label} → "
        f"{_fmt_amount(abs(target_change))} {token_label}"
    )

    desc_lines: list[str] = []
    if badge_text:
        desc_lines.append(badge_text)
    desc_lines.append(f"♻️ 取引：**{flow_text}**")
    if token_info and token_info.market_cap:
        desc_lines.append(f"📈 mcap：{_fmt_usd(float(token_info.market_cap))}")
    desc_lines.append(
        f"👥 SM buyers：**{distinct_buyers}** 人 (直近 {window_min} 分)"
    )
    desc_lines.append(f"💬 CA：`{target_mint}`")
    x_md = x_search_links_md(sym, target_mint)
    if x_md:
        desc_lines.append(f"🔍 X：{x_md}")
    desc_lines.append(f"🤖 {grok_token_link_md(sym, target_mint)}")
    desc_lines.append(f"🔗 Trade：{trade_links_md(target_mint, chain='solana')}")
    embed.description = "\n".join(desc_lines)

    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        try:
            embed.timestamp = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass

    return embed


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(SmSummaryCog(bot, config))
