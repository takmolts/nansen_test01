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
from bot.dexscreener_client import DexScreenerClient
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

        if config.sm_summary_enabled:
            self._auto_task = bot.loop.create_task(self._hourly_loop())
            logger.info(
                "sm_summary auto-loop 起動 (window=%d 分 / min_wallets=%d / top=%d / channel=%s)",
                config.sm_summary_window_min, config.sm_summary_min_wallets,
                config.sm_summary_top_n, config.sm_summary_channel_id,
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
        if not rows:
            return
        try:
            async with DexScreenerClient() as ds:
                async def _one(r: dict) -> None:
                    addr = r.get("target_mint")
                    if not isinstance(addr, str):
                        return
                    try:
                        data = await ds.get_token_data(addr)
                    except Exception:
                        logger.warning("DexScreener fetch 失敗 addr=%s", addr, exc_info=True)
                        return
                    if not isinstance(data, dict):
                        return
                    base = data.get("baseToken") if isinstance(data.get("baseToken"), dict) else None
                    if isinstance(base, dict):
                        sym = base.get("symbol")
                        if isinstance(sym, str):
                            r["dex_symbol"] = sym
                    mcap = data.get("marketCap") or data.get("fdv")
                    if isinstance(mcap, (int, float)):
                        r["dex_marketcap"] = float(mcap)
                    price = data.get("priceUsd")
                    if isinstance(price, (str, int, float)):
                        try:
                            r["dex_price_usd"] = float(price)
                        except (TypeError, ValueError):
                            pass

                await asyncio.gather(*(_one(r) for r in rows))
        except Exception:
            logger.exception("[sm_summary] DexScreener 補完失敗 (継続)")

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

        embed = _build_summary_embed(rows, window_min=window_min, tag=tag)
        try:
            await channel.send(embed=embed)
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


def _build_summary_embed(
    rows: list[dict], *, window_min: int, tag: str
) -> discord.Embed:
    title = "🛰️ Smart Wallet Signal Summary"
    if tag == "auto":
        title += f" (直近 {window_min} 分)"
    else:
        title += f" (手動 / 直近 {window_min} 分)"

    desc_lines = [
        f"群衆 ≥ {len(rows)} mint" if rows else "該当なし",
        f"スコア = 群衆×10 + 取引数×0.5 + 大口×5 + log10(USD proxy)",
    ]
    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=0xE91E63,
    )

    for i, r in enumerate(rows, start=1):
        addr = r.get("target_mint", "")
        sym = r.get("dex_symbol") or _short(addr)
        score = r.get("score", 0)
        n_buyers = r.get("distinct_buyers", 0)
        n_sellers = r.get("distinct_sellers", 0)
        buy_trades = r.get("buy_trades", 0)
        sell_trades = r.get("sell_trades", 0)
        n_large = r.get("n_large_buys", 0)
        sum_sol = float(r.get("sum_buy_sol") or 0)
        sum_stable = float(r.get("sum_buy_stable") or 0)
        mcap = r.get("dex_marketcap")
        last_ts = r.get("last_seen_ts")
        first_ts = r.get("first_seen_ts")

        buyers = r.get("buyers") or []
        sample = ", ".join(_short(b.get("wallet")) for b in buyers[:5])
        more = f" +{len(buyers)-5}" if len(buyers) > 5 else ""

        head = f"#{i} {sym}  (score {score:.1f})"

        value_lines = [
            f"🤝 buyers: **{n_buyers}** / 📊 trades: BUY **{buy_trades}** / SELL {sell_trades}"
            + (f" 🐋×{n_large}" if n_large else ""),
            f"💰 buy 合計: {_fmt_sol(sum_sol)} SOL + {_fmt_usd(sum_stable)} stable",
        ]
        if mcap:
            value_lines.append(f"📈 mcap: {_fmt_usd(float(mcap))}")
        if first_ts and last_ts:
            try:
                first_dt = datetime.fromtimestamp(int(first_ts), tz=JST)
                last_dt = datetime.fromtimestamp(int(last_ts), tz=JST)
                value_lines.append(
                    f"🕒 {first_dt.strftime('%H:%M')} → {last_dt.strftime('%H:%M')}"
                )
            except Exception:
                pass
        value_lines.append(f"👥 buyers: {sample}{more}")

        dex_url = f"https://dexscreener.com/solana/{addr}" if addr else "-"
        sol_url = f"https://solscan.io/token/{addr}" if addr else "-"
        value_lines.append(f"🔗 [DexScreener]({dex_url}) · [Solscan]({sol_url})")
        value_lines.append(f"`{addr}`")

        embed.add_field(name=head, value="\n".join(value_lines), inline=False)

    embed.set_footer(text=f"score 上位 {len(rows)} 件 / Powered by Helius + Nansen SM roster")
    return embed


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(SmSummaryCog(bot, config))
