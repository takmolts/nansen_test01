"""Bundle Safety カテゴリ (フェーズA簡略版)。

設計書: 3%超ホルダー数(30) + バンドル検出(40) + 警告ラベル(20) + Insider比(10) = 100点
フェーズA: 警告ラベル / Insider比 は profiler labels が必要なのでスキップ。 70 点を 100 点換算。
"""
from __future__ import annotations

from typing import Any

from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 13
RAW_MAX = 70  # whale_count(30) + bundle_detect(40)


def calculate(
    *,
    whales: list[dict[str, Any]],
    clusters: list[tuple[str, list[dict[str, Any]]]],
    weight_total_pct: int,
) -> CategoryScore:
    WEIGHT = RAW_WEIGHT_PCT / weight_total_pct
    """
    whales:   3%超ホルダーのリスト (deployer/CEX除外は MVP では未実装)
    clusters: [(funder, [holder_dict, ...]), ...] 2件以上のホルダーを共有する funder のみ
    """
    whale_count = len(whales)

    # 3%超ホルダー数 (max 30, 少ない = 安全)
    if whale_count == 0:
        wc_score = 30
    elif whale_count <= 2:
        wc_score = 25
    elif whale_count <= 5:
        wc_score = 15
    elif whale_count <= 10:
        wc_score = 5
    else:
        wc_score = 0

    # バンドル検出 (max 40, 大クラスタ = 危険)
    max_cluster = max((len(ws) for _, ws in clusters), default=1)
    if max_cluster <= 1:
        bd_score = 40
    elif max_cluster == 2:
        bd_score = 25
    elif max_cluster == 3:
        bd_score = 10
    else:
        bd_score = 0

    raw = wc_score + bd_score
    score = raw * 100 / RAW_MAX

    return CategoryScore(
        "Bundle Safety", "📦", score, WEIGHT,
        breakdown={
            "whale_count": whale_count,
            "cluster_count": len(clusters),
            "max_cluster_size": max_cluster,
            "wc_score": wc_score,
            "bd_score": bd_score,
            "raw_max": RAW_MAX,
        },
    )
