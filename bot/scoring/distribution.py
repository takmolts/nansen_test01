"""Distribution カテゴリ (フェーズA簡略版)。

設計書: 総ホルダー数(35) + Top10集中度(40) + 新規ホルダー増加率(25) = 100点
フェーズA: 新規増加率は別データなのでスキップ。 75 点を 100 点換算する。
"""
from __future__ import annotations

from typing import Any

from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 8
RAW_MAX = 75  # th(35) + t10(40)


def calculate(
    *,
    holder_pcts_desc: list[float],
    total_holders: int | None,
    weight_total_pct: int,
) -> CategoryScore:
    WEIGHT = RAW_WEIGHT_PCT / weight_total_pct
    """
    holder_pcts_desc: ホルダーの保有率を降順で並べたリスト (%単位)
    total_holders: トークン全体のホルダー数 (token-info の spot_metrics 由来)
    """
    if not holder_pcts_desc and total_holders is None:
        return CategoryScore("Distribution", "👥", 0.0, WEIGHT, note="データなし")

    # 総ホルダー数 (max 35)
    if total_holders is None:
        th_score = 0
    elif total_holders >= 5_000:
        th_score = 35
    elif total_holders >= 1_000:
        th_score = 25
    elif total_holders >= 500:
        th_score = 15
    elif total_holders >= 100:
        th_score = 8
    else:
        th_score = 2

    # Top10 集中度 (max 40, 集中ほど低)
    top10 = sum(holder_pcts_desc[:10])
    if top10 <= 20:
        t10_score = 40
    elif top10 <= 35:
        t10_score = 30
    elif top10 <= 50:
        t10_score = 20
    elif top10 <= 70:
        t10_score = 10
    else:
        t10_score = 0

    raw = th_score + t10_score
    score = raw * 100 / RAW_MAX

    return CategoryScore(
        "Distribution", "👥", score, WEIGHT,
        breakdown={
            "total_holders": total_holders,
            "top10_concentration_pct": top10,
            "th_score": th_score,
            "t10_score": t10_score,
            "raw_max": RAW_MAX,
        },
    )
