"""/check コマンド: Solana ミームコインの詳細を Nansen API から取得して Embed で返す。"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import embeds
from bot.config import Config
from bot.nansen_client import NansenAPIError, NansenClient

logger = logging.getLogger(__name__)

# バンドル検出のために related-wallets を取得する whale の上限
# (クレジット消費が whale 数ぶん増えるので上限をかける)
MAX_WHALE_LOOKUPS = 10
WHALE_THRESHOLD_PCT = 3.0


class CheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config

    @app_commands.command(
        name="check",
        description="Solana のミームコイン CA を入力すると、Nansen から詳細を取得します",
    )
    @app_commands.describe(ca="Solana トークンのコントラクトアドレス")
    async def check(self, interaction: discord.Interaction, ca: str):
        if (
            self.config.allowed_channel_ids
            and interaction.channel_id not in self.config.allowed_channel_ids
        ):
            await interaction.response.send_message(
                "このチャネルではコマンドが許可されていません。",
                ephemeral=True,
            )
            return

        ca = ca.strip()
        if not ca:
            await interaction.response.send_message("CA が空です。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            result = await _run_analysis(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
                token_address=ca,
            )
        except Exception:
            logger.exception("/check 実行中に想定外のエラー")
            await interaction.followup.send(
                "想定外のエラーが発生しました。ログを確認してください。",
                ephemeral=True,
            )
            return

        embeds.set_credit_footer(result.embed_list, result.credits_used)
        await interaction.followup.send(embeds=result.embed_list)


class _AnalysisResult:
    def __init__(self, embed_list: list[discord.Embed], credits_used: int):
        self.embed_list = embed_list
        self.credits_used = credits_used


async def _run_analysis(*, api_key: str, base_url: str, token_address: str) -> _AnalysisResult:
    async with NansenClient(api_key, base_url, chain="solana") as client:
        token_info_r, holders_r, sm_r = await asyncio.gather(
            client.token_information(token_address),
            client.holders(token_address),
            client.who_bought_sold(token_address),
            return_exceptions=True,
        )

        holders_list: list[dict] = []
        if not isinstance(holders_r, BaseException):
            holders_list = embeds.extract_holders(holders_r)

        whales = [
            h for h in holders_list
            if (embeds.holder_pct(h) or 0.0) >= WHALE_THRESHOLD_PCT
            and embeds.holder_address(h)
        ]
        whales_sorted = sorted(
            whales,
            key=lambda h: embeds.holder_pct(h) or 0.0,
            reverse=True,
        )
        whales_lookup = whales_sorted[:MAX_WHALE_LOOKUPS]

        clusters: list[tuple[str, list[dict]]] = []
        bundle_error: str | None = None
        if whales_lookup:
            try:
                related_results = await asyncio.gather(
                    *(client.related_wallets(embeds.holder_address(w)) for w in whales_lookup),
                    return_exceptions=True,
                )
            except Exception as e:
                bundle_error = str(e)
                related_results = []

            # funder_address -> [holder_dict, ...]
            funder_map: dict[str, list[dict]] = {}
            for whale, related in zip(whales_lookup, related_results):
                if isinstance(related, BaseException):
                    logger.warning(
                        "related-wallets 取得失敗 addr=%s: %s",
                        embeds.holder_address(whale),
                        related,
                    )
                    continue
                funders = embeds.find_first_funders(related)
                for f in funders:
                    funder_map.setdefault(f, []).append(whale)
            clusters = [(f, ws) for f, ws in funder_map.items() if len(ws) >= 2]
            clusters.sort(key=lambda c: len(c[1]), reverse=True)

        credits_used = client.credits_used

    embed_list: list[discord.Embed] = []

    embed_list.append(
        embeds.build_token_info_embed(
            None if isinstance(token_info_r, BaseException) else token_info_r,
            token_address,
            error=_err_msg(token_info_r),
        )
    )
    embed_list.append(
        embeds.build_smart_wallets_embed(
            None if isinstance(sm_r, BaseException) else sm_r,
            error=_err_msg(sm_r),
        )
    )
    embed_list.append(
        embeds.build_holders_embed(
            None if isinstance(holders_r, BaseException) else holders_r,
            error=_err_msg(holders_r),
        )
    )
    embed_list.append(
        embeds.build_bundle_embed(
            clusters=clusters,
            whales=whales_sorted,
            error=bundle_error,
        )
    )

    return _AnalysisResult(embed_list, credits_used)


def _err_msg(result: object) -> str | None:
    if not isinstance(result, BaseException):
        return None
    if isinstance(result, NansenAPIError):
        return f"HTTP {result.status}"
    return type(result).__name__


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(CheckCog(bot, config))
