"""スコア計算のエントリポイント。

cog から API レスポンスとバンドル検出の中間結果を受け取り、各カテゴリスコアと
総合スコアを TotalScore として返す。

フェーズB2: Risk 追加 (7カテゴリ、合計重み 85% を 100% に再正規化)
"""
from __future__ import annotations

from typing import Any

from bot.scoring import (
    bundle,
    deployer,
    distribution,
    liquidity,
    momentum,
    risk,
    smart_money,
)
from bot.scoring.types import TotalScore

# 現フェーズで実装済みカテゴリの設計書重みの合計 (%)
# SM 20 + Mom 12 + Liq 12 + Dist 8 + Bundle 13 + Deployer 8 + Risk 12 = 85
WEIGHT_TOTAL_PCT = 85


def calculate_scores(
    *,
    token_info: Any,
    sm_data: Any,
    holder_pcts_desc: list[float],
    total_holders: int | None,
    whales: list[dict[str, Any]],
    clusters: list[tuple[str, list[dict[str, Any]]]],
    deployer_address: str | None,
    deployer_fetched: bool,
    deployer_labels: Any,
    deployer_transactions: Any,
    deployer_pnl: Any,
    nansen_indicators: Any,
    flow_intelligence: Any,
) -> TotalScore:
    cats = [
        smart_money.calculate(sm_data, weight_total_pct=WEIGHT_TOTAL_PCT),
        momentum.calculate(token_info, weight_total_pct=WEIGHT_TOTAL_PCT),
        liquidity.calculate(token_info, weight_total_pct=WEIGHT_TOTAL_PCT),
        distribution.calculate(
            holder_pcts_desc=holder_pcts_desc,
            total_holders=total_holders,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        bundle.calculate(
            whales=whales,
            clusters=clusters,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        deployer.calculate(
            deployer_address=deployer_address,
            deployer_fetched=deployer_fetched,
            labels_resp=deployer_labels,
            transactions_resp=deployer_transactions,
            pnl_resp=deployer_pnl,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
        risk.calculate(
            token_info=token_info,
            nansen_indicators_resp=nansen_indicators,
            flow_intelligence_resp=flow_intelligence,
            weight_total_pct=WEIGHT_TOTAL_PCT,
        ),
    ]
    total = sum(c.score * c.weight for c in cats)
    total = max(0.0, min(100.0, total))
    return TotalScore(categories=cats, total=total)
