"""スコア計算のエントリポイント。

cog から API レスポンスとバンドル検出の中間結果を受け取り、各カテゴリスコアと
総合スコアを TotalScore として返す。
"""
from __future__ import annotations

from typing import Any

from bot.scoring import bundle, distribution, liquidity, momentum, smart_money
from bot.scoring.types import TotalScore


def calculate_scores(
    *,
    token_info: Any,
    sm_data: Any,
    holder_pcts_desc: list[float],
    total_holders: int | None,
    whales: list[dict[str, Any]],
    clusters: list[tuple[str, list[dict[str, Any]]]],
) -> TotalScore:
    cats = [
        smart_money.calculate(sm_data),
        momentum.calculate(token_info),
        liquidity.calculate(token_info),
        distribution.calculate(
            holder_pcts_desc=holder_pcts_desc,
            total_holders=total_holders,
        ),
        bundle.calculate(whales=whales, clusters=clusters),
    ]
    total = sum(c.score * c.weight for c in cats)
    # 重みは正規化済み (合計1.0) なので total は 0..100 に収まる想定
    total = max(0.0, min(100.0, total))
    return TotalScore(categories=cats, total=total)
