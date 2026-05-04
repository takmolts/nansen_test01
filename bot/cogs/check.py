"""/analyze コマンド: Solana ミームコインの詳細を Nansen API から取得して Embed で返す。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot import embeds
from bot import llm_client
from bot.coingecko_client import CoinGeckoClient
from bot.config import RESPONSE_MODE_INLINE, RESPONSE_MODE_THREAD, Config
from bot.dexscreener_client import DexScreenerClient
from bot.nansen_client import NansenAPIError, NansenClient
from bot.scoring import engine as scoring_engine
from bot.scoring.types import TotalScore
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
        name="analyze",
        description="Solana のミームコイン CA を入力すると、Nansen から詳細を取得します",
    )
    @app_commands.describe(
        ca="Solana トークンのコントラクトアドレス",
        thread="スレッドを作成して投稿するか (未指定時は環境変数 RESPONSE_MODE に従う、既定は inline)",
    )
    async def analyze(
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
                coingecko_api_key=(
                    self.config.coingecko_api_key
                    if self.config.coingecko_active
                    else None
                ),
                token_address=ca,
            )
        except Exception:
            logger.exception("/analyze 実行中に想定外のエラー")
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
    coingecko_api_key: str | None,
    token_address: str,
) -> _AnalysisResult:
    async with NansenClient(api_key, base_url, chain="solana") as client:
        (
            token_info_r,
            holders_r,
            sm_holders_r,
            sm_r,
            indicators_r,
            flow_r,
            flows_r,
        ) = await asyncio.gather(
            client.token_information(token_address),
            client.holders(token_address),
            client.holders_smart_money(token_address),
            client.who_bought_sold(token_address),
            client.nansen_indicators(token_address),
            client.flow_intelligence(token_address),
            client.flows(token_address, days=2),  # 24h 増加率算出のため最低 2 点取得
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

        # Narrative 用 DexScreener / CoinGecko を並列取得
        symbol_for_search = _extract_symbol(token_info_r)
        narrative_holder = {
            "similar_pairs": [],
            "is_dexscreener_boosted": None,
            "is_coingecko_trending": None,
        }
        await _populate_narrative(
            token_address=token_address,
            symbol=symbol_for_search,
            coingecko_api_key=coingecko_api_key,
            holder=narrative_holder,
        )

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
        sm_holders=None if isinstance(sm_holders_r, BaseException) else sm_holders_r,
        holder_pcts_desc=holder_pcts_desc,
        total_holders=total_holders,
        flows_resp=None if isinstance(flows_r, BaseException) else flows_r,
        whales=whales_sorted,
        clusters=clusters,
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

    # ローカル LLM の総括コメント (有効時のみ)
    if llm_client.is_enabled():
        try:
            sys_p, user_p = _build_llm_prompt(
                scores=scores,
                token_info=token_info_r,
                symbol=symbol,
                token_address=token_address,
            )
            comment = await llm_client.chat(sys_p, user_p)
            if comment.strip():
                embed_list.append(embeds.build_llm_summary_embed(comment))
        except Exception:
            logger.exception("総括コメント生成失敗")

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


_LLM_SYS_PROMPT = (
    "あなたは Solana ミームコインのオンチェーン分析アシスタント。\n"
    "渡されたデータをもとに、 日本語で 5〜7 行の総括コメントを書け。\n"
    "ルール:\n"
    "- 「結論」「注目点」「注意点」 の 3 セクションで箇条書き\n"
    "- 投資判断ではなく観察コメント。 「買い推奨」 等の表現は禁止\n"
    "- 数値は短く、 重要な要素のみ\n"
    "- 前置き・後書き・自己紹介・メタ発言禁止"
)


def _build_llm_prompt(
    *,
    scores: TotalScore,
    token_info: Any,
    symbol: str,
    token_address: str,
) -> tuple[str, str]:
    """system / user prompt を返す。 user prompt は ~1500 文字以内に抑える。"""
    info_data: Any = token_info.get("data") if isinstance(token_info, dict) else None
    if not isinstance(info_data, dict):
        info_data = token_info if isinstance(token_info, dict) else {}
    td = info_data.get("token_details") if isinstance(info_data.get("token_details"), dict) else {}
    spot = info_data.get("spot_metrics") if isinstance(info_data.get("spot_metrics"), dict) else {}

    name = info_data.get("name") or ""
    mcap_s = _short_usd(td.get("market_cap_usd"))
    liq_s = _short_usd(spot.get("liquidity_usd"))
    vol_s = _short_usd(spot.get("volume_total_usd"))
    holders = spot.get("total_holders")

    cats_lines: list[str] = []
    for c in scores.categories:
        kf = _llm_key_facts(c.name, c.breakdown or {})
        if kf:
            cats_lines.append(f"- {c.name}: {c.score:.0f}/100 ({kf})")
        else:
            cats_lines.append(f"- {c.name}: {c.score:.0f}/100")

    user_prompt = (
        f"銘柄: ${symbol} {name}\n"
        f"CA: {token_address}\n"
        f"MCap: {mcap_s} / Liq: {liq_s} / Vol24h: {vol_s} / Holders: {holders}\n\n"
        f"総合スコア: {scores.total:.1f}/100 ({scores.band})\n"
        + "\n".join(cats_lines)
        + "\n\nこれらをもとに総括コメントを作成。"
    )
    return _LLM_SYS_PROMPT, user_prompt


def _llm_key_facts(name: str, bd: dict) -> str:
    parts: list[str] = []
    if name == "Smart Money":
        sh = bd.get("sm_holder_count")
        nf = bd.get("smart_trader_net_flow_usd")
        nb = bd.get("new_buyers_count")
        if sh is not None:
            parts.append(f"SM保有={sh}")
        if isinstance(nf, (int, float)):
            parts.append(f"netflow={int(nf)}USD")
        if nb is not None:
            parts.append(f"新規={nb}")
    elif name == "Momentum":
        bsr = bd.get("buy_sell_ratio")
        if isinstance(bsr, (int, float)):
            parts.append(f"BS比={bsr:.2f}x")
    elif name == "Distribution":
        top10 = bd.get("top10_concentration_pct")
        if isinstance(top10, (int, float)):
            parts.append(f"Top10={top10:.1f}%")
        ng = bd.get("holder_growth_24h_ratio")
        if isinstance(ng, (int, float)):
            parts.append(f"24h増={ng*100:.2f}%")
    elif name == "Bundle Safety":
        wc = bd.get("whale_count")
        mcs = bd.get("max_cluster_size")
        if wc is not None:
            parts.append(f"whale={wc}")
        if mcs is not None:
            parts.append(f"max_cluster={mcs}")
    elif name == "Risk":
        btc = bd.get("btc_signal")
        cex = bd.get("cex_inflow_ratio")
        ad = bd.get("age_days")
        if btc:
            parts.append(f"BTC連動={btc}")
        if isinstance(cex, (int, float)):
            parts.append(f"CEX流入={cex*100:.1f}%")
        if ad is not None:
            parts.append(f"年齢={ad}d")
    elif name == "Narrative":
        status = bd.get("status")
        n = bd.get("similar_recent_count")
        if status:
            parts.append(f"status={status}")
        if n is not None:
            parts.append(f"類似={n}件")
    return ", ".join(parts)


def _short_usd(v) -> str:
    if v is None:
        return "?"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    a = abs(n)
    if a >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if a >= 1_000:
        return f"${n/1_000:.2f}K"
    return f"${n:.2f}"


async def setup(bot: commands.Bot) -> None:
    config: Config = bot.config  # type: ignore[attr-defined]
    await bot.add_cog(CheckCog(bot, config))
