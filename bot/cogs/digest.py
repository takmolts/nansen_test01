"""/digest コマンドと自動 digest 投稿。

- 手動: /digest [timeframe]  (デフォ 24h)
- 自動: 4 時間ごと (timeframe=6h) と JST 0:00 (timeframe=24h)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import archive_embeds
from bot.config import Config
from bot.digest_embeds import build_digest_summary_embed
from bot.dexscreener_client import DexScreenerClient
from bot.nansen_client import NansenClient
from bot.wallet_db import WalletDB

# pnl-leaderboard を叩く対象 token 数の上限 (各カテゴリ上位を統合してユニーク化)
WALLET_DB_PNL_TOP_TOKENS = 5
# pnl-leaderboard で取得する wallet 数
WALLET_DB_PNL_LIMIT = 20
# 蓄積条件: pnl_usd_total >= この値 / nof_trades >= この値
WALLET_DB_MIN_PNL = 100.0
WALLET_DB_MIN_TRADES = 3

logger = logging.getLogger(__name__)

TF_AUTO_4H = "6h"      # 4 時間 loop の screener timeframe (4h 直接非対応 → 6h で近似)
TF_AUTO_DAILY = "24h"  # 毎日 loop の screener timeframe
TF_DEFAULT = "24h"

JST = ZoneInfo("Asia/Tokyo")
JST_MIDNIGHT = time(0, 0, tzinfo=JST)
# 4 時間ごとの自動投稿時刻 (JST)
JST_4H_TIMES = [
    time(1, 0, tzinfo=JST),
    time(5, 0, tzinfo=JST),
    time(9, 0, tzinfo=JST),
    time(13, 0, tzinfo=JST),
    time(17, 0, tzinfo=JST),
    time(21, 0, tzinfo=JST),
]


class DigestCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config
        if not config.digest_channel_id:
            logger.info("DIGEST_CHANNEL_ID 未設定 → digest 自動投稿は無効")
            return

        # 個別 ON/OFF。 デフォは両方 OFF (手動 /digest のみ)。
        # 復活させたい場合は .env で DIGEST_AUTO_4H_ENABLED=true / DIGEST_AUTO_DAILY_ENABLED=true。
        if config.digest_auto_4h_enabled:
            self.auto_4h_digest.start()
            logger.info("digest auto 4h-loop 起動 (timeframe=%s)", TF_AUTO_4H)
        else:
            logger.info("digest auto 4h-loop は DISABLED (DIGEST_AUTO_4H_ENABLED=false)")

        if config.digest_auto_daily_enabled:
            self.auto_daily_digest.start()
            logger.info("digest auto daily-loop 起動 (timeframe=%s)", TF_AUTO_DAILY)
        else:
            logger.info("digest auto daily-loop は DISABLED (DIGEST_AUTO_DAILY_ENABLED=false)")

    def cog_unload(self) -> None:
        if self.auto_4h_digest.is_running():
            self.auto_4h_digest.cancel()
        if self.auto_daily_digest.is_running():
            self.auto_daily_digest.cancel()

    @app_commands.command(
        name="digest",
        description="勢い / SM / 急流入の 3 観点でミーム動向サマリを投稿します",
    )
    @app_commands.describe(
        timeframe="集計期間 (省略時は 24h)",
    )
    @app_commands.choices(
        timeframe=[
            app_commands.Choice(name="1時間", value="1h"),
            app_commands.Choice(name="6時間", value="6h"),
            app_commands.Choice(name="24時間 (デフォルト)", value="24h"),
            app_commands.Choice(name="7日", value="7d"),
            app_commands.Choice(name="30日", value="30d"),
        ]
    )
    async def digest(
        self,
        interaction: discord.Interaction,
        timeframe: app_commands.Choice[str] | None = None,
    ):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return

        tf = timeframe.value if timeframe else TF_DEFAULT
        await interaction.response.defer(thinking=True)

        try:
            result = await _build_digest(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
                timeframe=tf,
            )
        except Exception:
            logger.exception("/digest 実行中に想定外のエラー")
            await interaction.followup.send(
                "想定外のエラーが発生しました。 ログを確認してください。",
                ephemeral=True,
            )
            return

        # 通常結果は簡略 summary 1 embed を返信 (詳細は archive thread)
        # 1) summary を URL なしで先に送る (応答性のため)
        # 2) archive thread に詳細投稿 → ヘッダー Message を受け取る
        # 3) summary embed の description を「この集計の詳細」 ジャンプリンクで更新
        summary = build_digest_summary_embed(
            momentum_resp=result.momentum_resp,
            sm_resp=result.sm_resp,
            danger_resp=result.danger_resp,
            credits_used=result.credits_used,
            timeframe=tf,
        )
        summary_msg = await interaction.followup.send(embed=summary, wait=True)
        header_msg = await self._post_to_archive(result, timeframe=tf, tag="manual")
        if header_msg and summary_msg is not None:
            try:
                updated = build_digest_summary_embed(
                    momentum_resp=result.momentum_resp,
                    sm_resp=result.sm_resp,
                    danger_resp=result.danger_resp,
                    credits_used=result.credits_used,
                    timeframe=tf,
                    archive_jump_url=header_msg.jump_url,
                )
                await summary_msg.edit(embed=updated)
            except Exception:
                logger.exception("[manual] summary に jump_url を埋め込む edit 失敗")

        # wallet DB 蓄積
        await self._accumulate_wallets(
            api_key=self.config.nansen_api_key,
            base_url=self.config.nansen_base_url,
            result=result,
            tag="manual",
        )

    @app_commands.command(
        name="wallet-rank",
        description="蓄積済みの高勝率ウォレットランキングを表示します",
    )
    @app_commands.describe(
        order_by="ソート軸 (省略時はユニーク token 数)",
        limit="表示件数 (1〜25、 省略時 10)",
        min_pnl="最低累計 PnL USD (省略時 0)",
    )
    @app_commands.choices(
        order_by=[
            app_commands.Choice(name="ユニーク token 数 (持続性)", value="unique_tokens"),
            app_commands.Choice(name="累計 PnL", value="sum_pnl_usd"),
            app_commands.Choice(name="出現回数", value="total_appearances"),
        ]
    )
    async def wallet_rank(
        self,
        interaction: discord.Interaction,
        order_by: app_commands.Choice[str] | None = None,
        limit: int = 10,
        min_pnl: float = 0.0,
    ):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return

        order = order_by.value if order_by else "unique_tokens"
        limit = max(1, min(int(limit), 25))

        await interaction.response.defer(thinking=True)

        try:
            async with WalletDB() as db:
                rows = await db.top_wallets(order_by=order, limit=limit, min_pnl=min_pnl)
                total = await db.total_count()
        except Exception:
            logger.exception("/wallet-rank DB アクセス失敗")
            await interaction.followup.send(
                "DB アクセスでエラーが発生しました。 ログを確認してください。",
                ephemeral=True,
            )
            return

        if not rows:
            await interaction.followup.send(
                f"DB にはまだウォレットが蓄積されていません (累計 {total} 件)。",
                ephemeral=True,
            )
            return

        embed = _build_wallet_rank_embed(
            rows=rows,
            order_by=order,
            limit=limit,
            min_pnl=min_pnl,
            total_records=total,
        )
        await interaction.followup.send(embed=embed)

    # ---- 自動 4 時間ごと (JST 01 / 05 / 09 / 13 / 17 / 21) ----
    @tasks.loop(time=JST_4H_TIMES)
    async def auto_4h_digest(self) -> None:
        await self._auto_post(timeframe=TF_AUTO_4H, tag="4h-loop")

    @auto_4h_digest.before_loop
    async def _before_4h(self) -> None:
        await self.bot.wait_until_ready()

    # ---- 自動 毎日 JST 0:00 ----
    @tasks.loop(time=JST_MIDNIGHT)
    async def auto_daily_digest(self) -> None:
        await self._auto_post(timeframe=TF_AUTO_DAILY, tag="daily-loop")

    @auto_daily_digest.before_loop
    async def _before_daily(self) -> None:
        await self.bot.wait_until_ready()

    async def _auto_post(self, *, timeframe: str, tag: str) -> None:
        ch_id = self.config.digest_channel_id
        if not ch_id:
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except Exception:
                logger.exception("[%s] DIGEST_CHANNEL_ID=%s のチャンネル取得に失敗", tag, ch_id)
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning("[%s] チャンネル種別が不適切: %s", tag, type(channel).__name__)
            return

        try:
            result = await _build_digest(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
                timeframe=timeframe,
            )
            summary = build_digest_summary_embed(
                momentum_resp=result.momentum_resp,
                sm_resp=result.sm_resp,
                danger_resp=result.danger_resp,
                credits_used=result.credits_used,
                timeframe=timeframe,
            )
            summary_msg = await channel.send(embed=summary)
            header_msg = await self._post_to_archive(result, timeframe=timeframe, tag=tag)
            if header_msg:
                try:
                    updated = build_digest_summary_embed(
                        momentum_resp=result.momentum_resp,
                        sm_resp=result.sm_resp,
                        danger_resp=result.danger_resp,
                        credits_used=result.credits_used,
                        timeframe=timeframe,
                        archive_jump_url=header_msg.jump_url,
                    )
                    await summary_msg.edit(embed=updated)
                except Exception:
                    logger.exception("[%s] summary に jump_url を埋め込む edit 失敗", tag)
            await self._accumulate_wallets(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
                result=result,
                tag=tag,
            )
            logger.info("[%s] digest 投稿成功 (timeframe=%s)", tag, timeframe)
        except Exception:
            logger.exception("[%s] digest 自動投稿失敗", tag)

    async def _accumulate_wallets(
        self,
        *,
        api_key: str,
        base_url: str,
        result: "_DigestResult",
        tag: str,
    ) -> None:
        """digest 上位 token の pnl-leaderboard を取得して wallet DB に蓄積する。"""
        target_addresses = _pick_target_tokens(result, top=WALLET_DB_PNL_TOP_TOKENS)
        if not target_addresses:
            return

        async with NansenClient(api_key, base_url, chain="solana") as client:
            try:
                pnl_results = await asyncio.gather(
                    *(
                        client.pnl_leaderboard(addr, limit=WALLET_DB_PNL_LIMIT)
                        for addr in target_addresses
                    ),
                    return_exceptions=True,
                )
            except Exception:
                logger.exception("[%s] pnl-leaderboard 取得失敗", tag)
                return
            credits_used = client.credits_used

        # token_address → token_symbol の lookup を screener 結果から作る
        symbol_lookup = _build_symbol_lookup(result)

        rows: list[dict] = []
        for addr, resp in zip(target_addresses, pnl_results):
            if isinstance(resp, BaseException) or not isinstance(resp, dict):
                logger.warning("[%s] pnl-leaderboard %s 取得失敗", tag, addr)
                continue
            data = resp.get("data")
            if not isinstance(data, list):
                continue
            sym = symbol_lookup.get(addr.lower())
            for item in data:
                if not isinstance(item, dict):
                    continue
                trader = item.get("trader_address")
                if not isinstance(trader, str) or not trader:
                    continue
                pnl_total = _to_float(item.get("pnl_usd_total"))
                nof_trades = item.get("nof_trades")
                # 蓄積条件
                if pnl_total is None or pnl_total < WALLET_DB_MIN_PNL:
                    continue
                if not isinstance(nof_trades, (int, float)) or nof_trades < WALLET_DB_MIN_TRADES:
                    continue
                rows.append({
                    "wallet_address": trader,
                    "token_address": addr,
                    "token_symbol": sym,
                    "pnl_usd_realised": _to_float(item.get("pnl_usd_realised")),
                    "pnl_usd_unrealised": _to_float(item.get("pnl_usd_unrealised")),
                    "pnl_usd_total": pnl_total,
                    "nof_trades": int(nof_trades),
                    "label": item.get("trader_address_label"),
                })

        if not rows:
            logger.info("[%s] wallet DB: 蓄積条件を満たすレコードなし", tag)
            return

        try:
            async with WalletDB() as db:
                inserted = await db.insert_appearances(rows)
                total = await db.total_count()
            logger.info(
                "[%s] wallet DB: %d 件 insert 完了 / total=%d / pnl-leaderboard credit=%d",
                tag, inserted, total, credits_used,
            )
        except Exception:
            logger.exception("[%s] wallet DB 書き込み失敗", tag)

    async def _post_to_archive(
        self,
        result: "_DigestResult",
        *,
        timeframe: str,
        tag: str,
    ) -> discord.Message | None:
        """archive thread に区切りヘッダー + 各 token Embed を投稿し、 ヘッダー Message を返す。

        ヘッダー Message の `jump_url` を summary 側にぶら下げて 「この集計の詳細」 へ
        ジャンプできるようにするための返り値。 投稿失敗 / thread 無設定なら None。
        """
        thread_id = self.config.digest_archive_thread_id
        if not thread_id:
            return None
        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except Exception:
                logger.exception("[%s] DIGEST_ARCHIVE_THREAD_ID=%s の取得失敗", tag, thread_id)
                return None
        if not isinstance(thread, discord.Thread):
            logger.warning(
                "[%s] DIGEST_ARCHIVE_THREAD_ID=%s は Thread ではない (%s)",
                tag, thread_id, type(thread).__name__,
            )
            return None

        # 区切りヘッダー (集計時刻 + timeframe)
        now_jst = datetime.now(JST)
        header = (
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **[{now_jst.strftime('%Y-%m-%d %H:%M')} JST] 集計データ** "
            f"(timeframe=`{timeframe}`)"
        )
        header_msg: discord.Message | None = None
        try:
            header_msg = await thread.send(header)
        except Exception:
            logger.exception("[%s] archive ヘッダー投稿失敗", tag)

        for category, items in (
            ("momentum", result.momentum_data),
            ("sm", result.sm_data),
            ("hot", result.hot_data),
        ):
            for rank, t in enumerate(items, start=1):
                addr = t.get("token_address") or ""
                dex = result.dex_lookup.get(addr.lower()) if addr else None
                try:
                    embed = archive_embeds.build_archive_embed(
                        category=category,
                        rank=rank,
                        timeframe=timeframe,
                        screener_data=t,
                        dex_data=dex,
                    )
                    await thread.send(embed=embed)
                except Exception:
                    logger.exception(
                        "[%s] archive embed 投稿失敗 cat=%s addr=%s",
                        tag, category, addr,
                    )

        return header_msg


class _DigestResult:
    def __init__(
        self,
        momentum_resp,
        sm_resp,
        danger_resp,
        momentum_data: list[dict],
        sm_data: list[dict],
        hot_data: list[dict],
        dex_lookup: dict[str, dict | None],
        credits_used: int,
    ):
        self.momentum_resp = momentum_resp
        self.sm_resp = sm_resp
        self.danger_resp = danger_resp
        self.momentum_data = momentum_data
        self.sm_data = sm_data
        self.hot_data = hot_data
        self.dex_lookup = dex_lookup
        self.credits_used = credits_used


async def _build_digest(
    *,
    api_key: str,
    base_url: str,
    timeframe: str,
) -> _DigestResult:
    async with NansenClient(api_key, base_url, chain="solana") as client:
        momentum_r, sm_r, danger_r = await asyncio.gather(
            client.token_screener(
                token_age_days_max=30,
                timeframe=timeframe,
                sort_field="volume",
                sort_direction="DESC",
                limit=5,
            ),
            client.token_screener(
                trader_type="sm",
                timeframe=timeframe,
                sort_field="buy_volume",
                sort_direction="DESC",
                limit=5,
            ),
            client.token_screener(
                token_age_days_max=7,
                timeframe=timeframe,
                sort_field="volume",
                sort_direction="DESC",
                limit=20,
            ),
            return_exceptions=True,
        )
        credits_used = client.credits_used

    danger_sorted = _resort_by_inflow(danger_r, top_n=10)

    addresses = _collect_addresses(momentum_r, sm_r, danger_sorted)
    dex_lookup = await _fetch_dex_lookup(addresses)

    return _DigestResult(
        momentum_resp=momentum_r,
        sm_resp=sm_r,
        danger_resp=danger_sorted,
        momentum_data=_extract_top(momentum_r, 5),
        sm_data=_extract_top(sm_r, 5),
        hot_data=_extract_top(danger_sorted, 5),
        dex_lookup=dex_lookup,
        credits_used=credits_used,
    )


def _extract_top(resp, n: int) -> list[dict]:
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return []
    data = resp.get("data")
    if not isinstance(data, list):
        return []
    return [t for t in data[:n] if isinstance(t, dict)]


def _pick_target_tokens(result: "_DigestResult", *, top: int) -> list[str]:
    """各カテゴリ上位を統合してユニーク化した token_address を最大 top 件返す。"""
    seen: set[str] = set()
    out: list[str] = []
    for items in (result.momentum_data, result.sm_data, result.hot_data):
        for t in items:
            addr = t.get("token_address")
            if not isinstance(addr, str) or not addr:
                continue
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(addr)
            if len(out) >= top:
                return out
    return out


def _build_symbol_lookup(result: "_DigestResult") -> dict[str, str]:
    out: dict[str, str] = {}
    for items in (result.momentum_data, result.sm_data, result.hot_data):
        for t in items:
            addr = t.get("token_address")
            sym = t.get("token_symbol")
            if isinstance(addr, str) and isinstance(sym, str):
                out.setdefault(addr.lower(), sym)
    return out


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_ORDER_LABEL = {
    "unique_tokens": "ユニーク token 数",
    "sum_pnl_usd": "累計 PnL",
    "total_appearances": "出現回数",
}


def _build_wallet_rank_embed(
    *,
    rows,
    order_by: str,
    limit: int,
    min_pnl: float,
    total_records: int,
) -> discord.Embed:
    title = f"🏆 高勝率ウォレット (sort: {_ORDER_LABEL.get(order_by, order_by)})"
    desc_lines = [
        f"上位 {len(rows)} 件 / DB 蓄積 累計 {total_records} 件",
    ]
    if min_pnl > 0:
        desc_lines.append(f"min_pnl: ${min_pnl:.0f}")
    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=0xFFD700,
    )

    for i, r in enumerate(rows, start=1):
        addr = r["wallet_address"]
        label = r["label"] or ""
        unique_tokens = r["unique_tokens"]
        appearances = r["total_appearances"]
        sum_pnl = r["sum_pnl_usd"]
        last_seen = r["last_seen"]

        short = f"{addr[:4]}...{addr[-4:]}" if len(addr) > 8 else addr
        head = f"#{i} {label}".strip() if label else f"#{i} {short}"
        nansen_url = f"https://app.nansen.ai/profiler/{addr}?chain=solana"
        solscan_url = f"https://solscan.io/account/{addr}"

        value_lines = [
            f"📍 [{short}]({solscan_url}) · [Nansen]({nansen_url})",
            f"🪙 unique tokens: **{unique_tokens}** | 📊 出現: **{appearances}** 回",
            f"💵 累計 PnL: **{_fmt_pnl(sum_pnl)}**",
            f"🕒 last seen: {last_seen[:16] if isinstance(last_seen, str) else last_seen}",
        ]
        embed.add_field(
            name=head,
            value="\n".join(value_lines),
            inline=False,
        )

    return embed


def _fmt_pnl(v) -> str:
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    a = abs(n)
    sign = "-" if n < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a/1_000:.2f}K"
    return f"{sign}${a:.2f}"


def _collect_addresses(*responses) -> list[str]:
    """全レスポンスから上位 5 件分の token_address をユニークに集める。"""
    seen: set[str] = set()
    out: list[str] = []
    for resp in responses:
        if isinstance(resp, BaseException) or not isinstance(resp, dict):
            continue
        data = resp.get("data")
        if not isinstance(data, list):
            continue
        for t in data[:5]:
            if not isinstance(t, dict):
                continue
            addr = t.get("token_address")
            if isinstance(addr, str) and addr:
                key = addr.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(addr)
    return out


async def _fetch_dex_lookup(addresses: list[str]) -> dict[str, dict | None]:
    """DexScreener から各 token の pair データを並列取得し lowercase アドレス → dict の lookup を返す。"""
    if not addresses:
        return {}
    results: dict[str, dict | None] = {a.lower(): None for a in addresses}
    try:
        async with DexScreenerClient() as ds:
            async def _one(a: str) -> tuple[str, dict | None]:
                try:
                    return a.lower(), await ds.get_token_data(a)
                except Exception:
                    logger.warning("DexScreener token data 取得失敗 addr=%s", a, exc_info=True)
                    return a.lower(), None

            pairs = await asyncio.gather(*(_one(a) for a in addresses))
            for k, v in pairs:
                results[k] = v
    except Exception:
        logger.exception("DexScreener セッション初期化失敗")
    return results


def _resort_by_inflow(resp, top_n: int):
    """token_screener レスポンスを inflow_fdv_ratio 降順で並べ直して上位 top_n 件にする。"""
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return resp
    data = resp.get("data") if isinstance(resp.get("data"), list) else []
    items = [x for x in data if isinstance(x, dict)]

    def _ratio(t: dict) -> float:
        v = t.get("inflow_fdv_ratio")
        try:
            return float(v) if v is not None else -1.0
        except (TypeError, ValueError):
            return -1.0

    items.sort(key=_ratio, reverse=True)
    return {**resp, "data": items[:top_n]}


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(DigestCog(bot, config))
