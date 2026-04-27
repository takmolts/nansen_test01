"""スコア計算のエントリポイント。

Deployer Trust は Nansen profiler / Helius 双方ともデータが薄く、
スコアとして機能しないため評価項目から除外している。

現フェーズ: 7 カテゴリのうち Deployer Trust を抜いた 6 カテゴリ
SM 20 + Mom 12 + Liq 12 + Dist 8 + Bundle 13 + Risk 12 + Narr 15 = 92
"""
from __future__ import annotations

from typing import Any

from bot.scoring import (
    bundle,
    distribution,
    liquidity,
    momentum,
    narrative,
    risk,
    smart_money,
)
from bot.scoring.types import TotalScore

WEIGHT_TOTAL_PCT = 92


def calculate_scores(
    *,
    token_address: str,
    token_info: Any,
    sm_data: Any,
    sm_holders: Any,
    holder_pcts_desc: list[float],
    total_holders: int | None,
    flows_resp: Any,
    whales: list[dict[str, Any]],
    clusters: list[tuple[str, list[dict[str, Any]]]],
    nansen_indicators: Any,
    flow_intelligence: Any,
    similar_pairs: list[dict[str, Any]] | None,
    is_dexscreener_boosted: bool | None,
    is_coingecko_trending: bool | None,
) -> TotalScore:
    cats = [
        smart_money.calculate(
            sm_holders_resp=sm_holders,
            flow_intelligence_resp=flow_intelligence,
            who_bought_sold_resp=sm_data,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        momentum.calculate(token_info, weight_total_pct=WEIGHT_TOTAL_PCT),
        liquidity.calculate(token_info, weight_total_pct=WEIGHT_TOTAL_PCT),
        distribution.calculate(
            holder_pcts_desc=holder_pcts_desc,
            total_holders=total_holders,
            flows_resp=flows_resp,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        bundle.calculate(
            whales=whales,
            clusters=clusters,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        risk.calculate(
            token_info=token_info,
            nansen_indicators_resp=nansen_indicators,
            flow_intelligence_resp=flow_intelligence,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        narrative.calculate(
            token_info=token_info,
            token_address=token_address,
            similar_pairs=similar_pairs,
            is_dexscreener_boosted=is_dexscreener_boosted,
            is_coingecko_trending=is_coingecko_trending,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
    ]
    total = sum(c.score * c.weight for c in cats)
    total = max(0.0, min(100.0, total))
    return TotalScore(categories=cats, total=total)
