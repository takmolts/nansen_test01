"""/check コマンド: Solana ミームコインの詳細を Nansen API から取得して Embed で返す。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot import embeds
from bot.coingecko_client import CoinGeckoClient
from bot.config import RESPONSE_MODE_INLINE, RESPONSE_MODE_THREAD, Config
from bot.dexscreener_client import DexScreenerClient
from bot.helius_client import HeliusClient
from bot.nansen_client import NansenAPIError, NansenClient
from bot.scoring import engine as scoring_engine
from bot.scoring.types import TotalScore
from bot.solana_rpc import SolanaRPCClient, SolanaRPCError
from bot.views import ResultView

logger = logging.getLogger(__name__)

# バンドル検出のために related-wallets を取得する whale の上限
# (クレジット消費が whale 数ぶん増えるので上限をかける)
MAX_WHALE_LOOKUPS = 10
WHALE_THRESHOLD_PCT = 3.0

# Discord のスレッド名は最大 100 文字
THREAD_NAME_MAX = 100


class CheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config

    @app_commands.command(
        name="check",
        description="Solana のミームコイン CA を入力すると、Nansen から詳細を取得します",
    )
    @app_commands.describe(
        ca="Solana トークンのコントラクトアドレス",
        thread="スレッドを作成して投稿するか (未指定時は環境変数 RESPONSE_MODE に従う、既定は inline)",
    )
    async def check(
        self,
        interaction: discord.Interaction,
        ca: str,
        thread: bool | None = None,
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

        ca = ca.strip()
        if not ca:
            await interaction.response.send_message("CA が空です。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            result = await _run_analysis(
                api_key=self.config.nansen_api_key,
                base_url=self.config.nansen_base_url,
                solana_rpc_url=self.config.solana_rpc_url,
                coingecko_api_key=self.config.coingecko_api_key,
                helius_api_key=self.config.helius_api_key,
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

        # コマンド引数 thread が指定されていればそれを優先、無ければ env の値
        if thread is True:
            response_mode = RESPONSE_MODE_THREAD
        elif thread is False:
            response_mode = RESPONSE_MODE_INLINE
        else:
            response_mode = self.config.response_mode

        await _post_result(
            interaction=interaction,
            result=result,
            token_address=ca,
            response_mode=response_mode,
        )


class _AnalysisResult:
    def __init__(
        self,
        embed_list: list[discord.Embed],
        credits_used: int,
        symbol: str,
        scores: TotalScore | None,
    ):
        self.embed_list = embed_list
        self.credits_used = credits_used
        self.symbol = symbol
        self.scores = scores


async def _run_analysis(
    *,
    api_key: str,
    base_url: str,
    solana_rpc_url: str,
    coingecko_api_key: str | None,
    helius_api_key: str | None,
    token_address: str,
) -> _AnalysisResult:
    async with NansenClient(api_key, base_url, chain="solana") as client:
        # 並列: token-info / holders / who-bought-sold + deployer 取得
        async def _fetch_deployer() -> tuple[bool, str | None]:
            # 1. Helius が使えればそちら優先 (creator を直接取れる)
            if helius_api_key:
                try:
                    async with HeliusClient(helius_api_key) as helius:
                        creator = await helius.get_creator(token_address)
                        if creator:
                            return True, creator
                except Exception:
                    logger.exception("Helius creator 取得失敗 → Solana RPC で fallback")

            # 2. Solana RPC で mintAuthority
            try:
                async with SolanaRPCClient(solana_rpc_url) as rpc:
                    return await rpc.fetch_mint_authority(token_address)
            except Exception:
                logger.exception("mint authority 取得失敗")
                return False, None

        (
            token_info_r,
            holders_r,
            sm_r,
            deployer_result,
            indicators_r,
            flow_r,
        ) = await asyncio.gather(
            client.token_information(token_address),
            client.holders(token_address),
            client.who_bought_sold(token_address),
            _fetch_deployer(),
            client.nansen_indicators(token_address),
            client.flow_intelligence(token_address),
            return_exceptions=True,
        )
        if isinstance(deployer_result, BaseException):
            deployer_fetched, deployer_addr = False, None
        else:
            deployer_fetched, deployer_addr = deployer_result

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

        # Deployer Trust 用データ取得 (mintAuthority があるときのみ) と
        # Narrative 用 DexScreener / CoinGecko を並列取得
        deployer_labels_r: Any = None
        deployer_tx_r: Any = None
        deployer_pnl_r: Any = None

        symbol_for_search = _extract_symbol(token_info_r)
        narrative_holder = {
            "similar_pairs": [],
            "is_dexscreener_boosted": None,
            "is_coingecko_trending": None,
        }

        async def _fetch_deployer_profiler() -> None:
            nonlocal deployer_labels_r, deployer_tx_r, deployer_pnl_r
            if not (isinstance(deployer_addr, str) and deployer_addr):
                return
            deployer_labels_r, deployer_tx_r, deployer_pnl_r = await asyncio.gather(
                client.labels(deployer_addr),
                client.oldest_transaction(deployer_addr),
                client.pnl_summary(deployer_addr),
                return_exceptions=True,
            )

        async def _fetch_narrative() -> None:
            await _populate_narrative(
                token_address=token_address,
                symbol=symbol_for_search,
                coingecko_api_key=coingecko_api_key,
                holder=narrative_holder,
            )

        await asyncio.gather(_fetch_deployer_profiler(), _fetch_narrative())

        credits_used = client.credits_used

    symbol = _extract_symbol(token_info_r)
    total_holders = _extract_total_holders(token_info_r)

    holder_pcts_desc = sorted(
        [embeds.holder_pct(h) or 0.0 for h in holders_list],
        reverse=True,
    )
    scores = scoring_engine.calculate_scores(
        token_address=token_address,
        token_info=None if isinstance(token_info_r, BaseException) else token_info_r,
        sm_data=None if isinstance(sm_r, BaseException) else sm_r,
        holder_pcts_desc=holder_pcts_desc,
        total_holders=total_holders,
        whales=whales_sorted,
        clusters=clusters,
        deployer_address=deployer_addr if isinstance(deployer_addr, str) else None,
        deployer_fetched=deployer_fetched,
        deployer_labels=None if isinstance(deployer_labels_r, BaseException) else deployer_labels_r,
        deployer_transactions=None if isinstance(deployer_tx_r, BaseException) else deployer_tx_r,
        deployer_pnl=None if isinstance(deployer_pnl_r, BaseException) else deployer_pnl_r,
        nansen_indicators=None if isinstance(indicators_r, BaseException) else indicators_r,
        flow_intelligence=None if isinstance(flow_r, BaseException) else flow_r,
        similar_pairs=narrative_holder.get("similar_pairs"),
        is_dexscreener_boosted=narrative_holder.get("is_dexscreener_boosted"),
        is_coingecko_trending=narrative_holder.get("is_coingecko_trending"),
    )

    embed_list: list[discord.Embed] = []
    embed_list.append(embeds.build_summary_embed(scores, symbol))
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
            total_holders_override=total_holders,
        )
    )
    embed_list.append(
        embeds.build_bundle_embed(
            clusters=clusters,
            whales=whales_sorted,
            error=bundle_error,
        )
    )

    return _AnalysisResult(embed_list, credits_used, symbol, scores)


def _extract_symbol(token_info: Any) -> str:
    """token-information レスポンスから symbol を抜き出す(スレッド名用)。"""
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return ""
    data = token_info.get("data")
    if not isinstance(data, dict):
        data = token_info
    sym = data.get("symbol") or data.get("token_symbol") or ""
    return str(sym).strip()


def _extract_total_holders(token_info: Any) -> int | None:
    """token-information レスポンスから総ホルダー数を抜き出す。"""
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return None
    data = token_info.get("data")
    if not isinstance(data, dict):
        data = token_info
    spot = data.get("spot_metrics") if isinstance(data.get("spot_metrics"), dict) else {}
    raw = spot.get("total_holders") if spot else None
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _populate_narrative(
    *,
    token_address: str,
    symbol: str,
    coingecko_api_key: str | None,
    holder: dict[str, Any],
) -> None:
    """DexScreener / CoinGecko から Narrative 用データを取得し holder に書き込む。"""
    async def _ds_part() -> None:
        try:
            async with DexScreenerClient() as ds:
                if symbol:
                    try:
                        holder["similar_pairs"] = await ds.search(symbol)
                    except Exception:
                        logger.exception("DexScreener search 失敗")
                try:
                    holder["is_dexscreener_boosted"] = await ds.is_boosted(token_address)
                except Exception:
                    logger.exception("DexScreener is_boosted 失敗")
        except Exception:
            logger.exception("DexScreener セッション初期化失敗")

    async def _cg_part() -> None:
        if not coingecko_api_key:
            # キー未設定時は trending 判定スキップ
            return
        try:
            async with CoinGeckoClient(coingecko_api_key) as cg:
                coin = await cg.get_coin_by_contract("solana", token_address)
                cg_id = coin.get("id") if isinstance(coin, dict) else None
                if not cg_id:
                    holder["is_coingecko_trending"] = False
                    return
                trending = await cg.trending_coin_ids()
                holder["is_coingecko_trending"] = cg_id in trending
        except Exception:
            logger.exception("CoinGecko 取得失敗")
            holder["is_coingecko_trending"] = None

    await asyncio.gather(_ds_part(), _cg_part())


def _err_msg(result: object) -> str | None:
    if not isinstance(result, BaseException):
        return None
    if isinstance(result, NansenAPIError):
        return f"HTTP {result.status}"
    return type(result).__name__


async def _post_result(
    *,
    interaction: discord.Interaction,
    result: _AnalysisResult,
    token_address: str,
    response_mode: str,
) -> None:
    owner_id = interaction.user.id

    if response_mode != RESPONSE_MODE_THREAD:
        view = ResultView(owner_id=owner_id, scores=result.scores)
        await interaction.followup.send(embeds=result.embed_list, view=view)
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "スレッド作成不可なチャンネル種別 (%s) → inline 投稿に fallback",
            type(channel).__name__,
        )
        view = ResultView(owner_id=owner_id, scores=result.scores)
        await interaction.followup.send(embeds=result.embed_list, view=view)
        return

    base_name = f"${result.symbol}" if result.symbol else f"Check {token_address[:8]}"
    thread_name = base_name[:THREAD_NAME_MAX]

    # 同名スレッドがあれば追記モード
    existing = await _find_existing_thread(channel, thread_name)
    if existing is not None:
        try:
            if existing.archived:
                await existing.edit(archived=False)
            view = ResultView(
                owner_id=owner_id,
                target_thread=existing,
                scores=result.scores,
            )
            await existing.send(embeds=result.embed_list, view=view)
            await interaction.followup.send(
                content=f"📊 既存スレッド {existing.mention} に追記しました",
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("既存スレッドへの追記失敗 → 新規作成に fallback")

    # 新規スレッド作成
    try:
        anchor_webhook = await interaction.followup.send(
            content=f"📊 {base_name} の分析結果はスレッド内に投稿しました",
            wait=True,
        )
        # WebhookMessage には guild 情報が付かないため Message に取り直す
        anchor = await channel.fetch_message(anchor_webhook.id)
        thread = await anchor.create_thread(name=thread_name)
        view = ResultView(
            owner_id=owner_id,
            target_thread=thread,
            target_anchor=anchor,
            scores=result.scores,
        )
        await thread.send(embeds=result.embed_list, view=view)
    except discord.Forbidden:
        logger.exception("スレッド作成権限不足")
        await interaction.followup.send(
            "スレッド作成権限がありません。Bot の権限を確認してください。",
            ephemeral=True,
        )
    except Exception:
        logger.exception("スレッド投稿失敗 → inline に fallback")
        view = ResultView(owner_id=owner_id, scores=result.scores)
        await interaction.followup.send(embeds=result.embed_list, view=view)


async def _find_existing_thread(
    channel: discord.TextChannel,
    name: str,
) -> discord.Thread | None:
    """同名スレッドを検索する(アクティブ → アーカイブ済みの順)。"""
    for t in channel.threads:
        if t.name == name:
            return t
    try:
        async for t in channel.archived_threads(limit=50):
            if t.name == name:
                return t
    except discord.Forbidden:
        logger.warning("アーカイブスレッド一覧の取得権限なし")
    except Exception:
        logger.exception("アーカイブスレッド検索で例外")
    return None


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(CheckCog(bot, config))
