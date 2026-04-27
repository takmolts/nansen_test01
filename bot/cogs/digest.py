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
from bot.digest_embeds import build_digest_message_groups
from bot.dexscreener_client import DexScreenerClient
from bot.nansen_client import NansenClient

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
        if config.digest_channel_id:
            self.auto_4h_digest.start()
            self.auto_daily_digest.start()
            logger.info(
                "Digest auto-loop 起動: 4h=%s, daily=%s, channel=%s",
                TF_AUTO_4H, TF_AUTO_DAILY, config.digest_channel_id,
            )
        else:
            logger.info("DIGEST_CHANNEL_ID 未設定 → 自動投稿は無効")

    def cog_unload(self) -> None:
        self.auto_4h_digest.cancel()
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

        # 通常結果を返信
        for embeds_in_msg in result.groups:
            if embeds_in_msg:
                await interaction.followup.send(embeds=embeds_in_msg)

        # アーカイブスレッドにも追記
        await self._post_to_archive(result, timeframe=tf, tag="manual")

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
            for embeds_in_msg in result.groups:
                if embeds_in_msg:
                    await channel.send(embeds=embeds_in_msg)
            await self._post_to_archive(result, timeframe=timeframe, tag=tag)
            logger.info("[%s] digest 投稿成功 (timeframe=%s)", tag, timeframe)
        except Exception:
            logger.exception("[%s] digest 自動投稿失敗", tag)

    async def _post_to_archive(
        self,
        result: "_DigestResult",
        *,
        timeframe: str,
        tag: str,
    ) -> None:
        thread_id = self.config.digest_archive_thread_id
        if not thread_id:
            return
        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except Exception:
                logger.exception("[%s] DIGEST_ARCHIVE_THREAD_ID=%s の取得失敗", tag, thread_id)
                return
        if not isinstance(thread, discord.Thread):
            logger.warning(
                "[%s] DIGEST_ARCHIVE_THREAD_ID=%s は Thread ではない (%s)",
                tag, thread_id, type(thread).__name__,
            )
            return

        # 区切りヘッダー (集計時刻 + timeframe)
        now_jst = datetime.now(JST)
        header = (
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **[{now_jst.strftime('%Y-%m-%d %H:%M')} JST] 集計データ** "
            f"(timeframe=`{timeframe}`)"
        )
        try:
            await thread.send(header)
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


class _DigestResult:
    def __init__(
        self,
        groups: list[list[discord.Embed]],
        momentum_data: list[dict],
        sm_data: list[dict],
        hot_data: list[dict],
        dex_lookup: dict[str, dict | None],
    ):
        self.groups = groups
        self.momentum_data = momentum_data
        self.sm_data = sm_data
        self.hot_data = hot_data
        self.dex_lookup = dex_lookup


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
    image_urls = {k: (v.get("info", {}).get("imageUrl") if isinstance(v, dict) else None)
                  for k, v in dex_lookup.items()}

    groups = build_digest_message_groups(
        momentum_resp=momentum_r,
        sm_resp=sm_r,
        danger_resp=danger_sorted,
        image_urls=image_urls,
        credits_used=credits_used,
        timeframe=timeframe,
    )

    return _DigestResult(
        groups=groups,
        momentum_data=_extract_top(momentum_r, 5),
        sm_data=_extract_top(sm_r, 5),
        hot_data=_extract_top(danger_sorted, 5),
        dex_lookup=dex_lookup,
    )


def _extract_top(resp, n: int) -> list[dict]:
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return []
    data = resp.get("data")
    if not isinstance(data, list):
        return []
    return [t for t in data[:n] if isinstance(t, dict)]


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
