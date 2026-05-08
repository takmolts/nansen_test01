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

from bot.config import Config
from bot.links import grok_token_link_md, trade_links_md, x_search_links_md
from bot.token_info import get_token_infos
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
        ch_id = self.config.sm_summary_channel_id
        if not ch_id:
            logger.warning("[sm_summary] 投稿先未設定 → 通知スキップ")
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except Exception:
                logger.exception("[sm_summary] channel fetch 失敗 id=%s", ch_id)
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "[sm_summary] channel %s が TextChannel/Thread ではない (%s)",
                ch_id, type(channel).__name__,
            )
            return

        # ヘッダ + token ごとに 1 embed (Discord は 1 message に最大 10 embed)
        rows = rows[:10]
        header = _build_header_text(rows, window_min=window_min, tag=tag)
        embeds = [_build_token_embed(r, rank=i + 1) for i, r in enumerate(rows)]
        try:
            await channel.send(content=header, embeds=embeds)
        except Exception:
            logger.exception("[sm_summary] 投稿失敗")

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
        await interaction.followup.send("集計を投稿しました。")


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

    desc_parts = [f"score **{score:.1f}**"]
    if first_ts and last_ts:
        try:
            first_dt = datetime.fromtimestamp(int(first_ts), tz=JST)
            last_dt = datetime.fromtimestamp(int(last_ts), tz=JST)
            desc_parts.append(
                f"🕒 {first_dt.strftime('%H:%M')}〜{last_dt.strftime('%H:%M')}"
            )
        except Exception:
            pass

    embed = discord.Embed(
        title=title,
        description="  ·  ".join(desc_parts),
        color=0xE91E63,
    )
    if isinstance(img, str) and img.startswith("http"):
        embed.set_thumbnail(url=img)

    # 💵 SM buy
    buy_value = (
        f"{_fmt_usd(sum_value_usd)}  "
        f"({_fmt_sol(sum_sol)} SOL + {_fmt_usd(sum_stable)} stable)"
    )
    embed.add_field(name="💵 SM buy", value=buy_value, inline=False)

    # 👥 traders
    sample = ", ".join(_short(b.get("wallet")) for b in buyers[:5])
    more = f" +{len(buyers)-5}" if len(buyers) > 5 else ""
    traders_lines = [f"BUY: **{n_buyers}**  ({sample}{more})"]
    if n_sellers or sell_trades:
        traders_lines.append(f"SELL: {n_sellers} wallet / {sell_trades} trades")
    if n_large:
        traders_lines.append(f"🐋 大口 BUY: ×{n_large}")
    embed.add_field(name="👥 traders", value="\n".join(traders_lines), inline=False)

    # 📈 mcap
    if mcap:
        embed.add_field(name="📈 mcap", value=_fmt_usd(float(mcap)), inline=False)

    # 💬 CA (full)
    if addr:
        embed.add_field(name="💬 CA", value=f"`{addr}`", inline=False)

        # 🔍 X Search
        x_md = x_search_links_md(sym, addr)
        if x_md:
            embed.add_field(name="🔍 X Search", value=x_md, inline=False)

        # 🤖 Grok
        embed.add_field(
            name="🤖 Grok",
            value=grok_token_link_md(sym, addr),
            inline=False,
        )

        # 🔗 Trade
        embed.add_field(
            name="🔗 Trade",
            value=trade_links_md(addr, chain="solana"),
            inline=False,
        )

    return embed


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(SmSummaryCog(bot, config))
