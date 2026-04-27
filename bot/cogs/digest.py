"""/digest コマンドと自動 digest 投稿。

- 手動: /digest [timeframe]  (デフォ 24h)
- 自動: 4 時間ごと (timeframe=6h) と JST 0:00 (timeframe=24h)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import time, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import Config
from bot.digest_embeds import build_digest_embeds
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
            embed_list = await _build_digest(
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

        await interaction.followup.send(embeds=embed_list)

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
            embed_list = await _build_digest(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
                timeframe=timeframe,
            )
            await channel.send(embeds=embed_list)
            logger.info("[%s] digest 投稿成功 (timeframe=%s)", tag, timeframe)
        except Exception:
            logger.exception("[%s] digest 自動投稿失敗", tag)


async def _build_digest(
    *,
    api_key: str,
    base_url: str,
    timeframe: str,
) -> list[discord.Embed]:
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

    danger_sorted = _resort_by_inflow(danger_r, top_n=5)

    return build_digest_embeds(
        momentum_resp=momentum_r,
        sm_resp=sm_r,
        danger_resp=danger_sorted,
        credits_used=credits_used,
        timeframe=timeframe,
    )


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
