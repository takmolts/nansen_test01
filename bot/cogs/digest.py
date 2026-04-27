"""/digest コマンド: ミーム動向のサマリを 3 Embed で投稿。

集計内容:
- 🔥 出来高急増ミーム (token-screener: age<=30d, sort=volume DESC)
- 🧠 SM 買い集めランキング (token-screener: trader_type=sm, sort=buy_volume DESC)
- 🚨 警戒トークン (token-screener: age<=7d, sort=inflow_fdv_ratio DESC)
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Config
from bot.digest_embeds import build_digest_embeds
from bot.nansen_client import NansenClient

logger = logging.getLogger(__name__)


class DigestCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config

    @app_commands.command(
        name="digest",
        description="勢い / SM / 警戒の 3 観点でミーム動向サマリを投稿します",
    )
    async def digest(self, interaction: discord.Interaction):
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
            embed_list = await _build_digest(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
            )
        except Exception:
            logger.exception("/digest 実行中に想定外のエラー")
            await interaction.followup.send(
                "想定外のエラーが発生しました。 ログを確認してください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embeds=embed_list)


async def _build_digest(*, api_key: str, base_url: str) -> list[discord.Embed]:
    async with NansenClient(api_key, base_url, chain="solana") as client:
        momentum_r, sm_r, danger_r = await asyncio.gather(
            client.token_screener(
                token_age_days_max=30,
                sort_field="volume",
                sort_direction="DESC",
                limit=5,
            ),
            client.token_screener(
                trader_type="sm",
                sort_field="buy_volume",
                sort_direction="DESC",
                limit=5,
            ),
            # 警戒トークン: age<=7d で出来高大の中から、 client 側で
            # inflow_fdv_ratio 降順に並べ替えて上位 5 件を取り出す
            # (一部の field は server 側 sort 非対応のため)
            client.token_screener(
                token_age_days_max=7,
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
    )


def _resort_by_inflow(resp, top_n: int):
    """token_screener レスポンスを inflow_fdv_ratio 降順で並べ直して上位 top_n 件にする。

    例外オブジェクトはそのまま返して上位の表示で error 扱いにする。
    """
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
