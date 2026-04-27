"""Distribution カテゴリ (フェーズB3 完成版)。

設計書通り 3 軸:
- 総ホルダー数 (max 35) ← token-info の spot_metrics.total_holders
- Top10 集中度 (max 40) ← holders レスポンスの ownership_percentage 合計
- 24h 新規ホルダー増加率 (max 25) ← /tgm/flows の holders_count 推移
"""
from __future__ import annotations

from typing import Any

from bot.scoring._helpers import to_float
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 8


def calculate(
    *,
    holder_pcts_desc: list[float],
    total_holders: int | None,
    flows_resp: Any,
    weight_total_pct: int,
) -> CategoryScore:
    if not holder_pcts_desc and total_holders is None:
        return CategoryScore("Distribution", "👥", 0.0, RAW_WEIGHT_PCT / weight_total_pct, note="データなし")

    weight = RAW_WEIGHT_PCT / weight_total_pct

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

    # Top10 集中度 (max 40)
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

    # 24h 新規ホルダー増加率 (max 25)
    nh_ratio = _holder_growth_24h(flows_resp, total_holders)
    if nh_ratio is None:
        nh_score = 0.0
    else:
        nh_score = min(max(nh_ratio, 0.0) / 0.05, 1.0) * 25  # 5%/24h で満点

    score = float(th_score + t10_score + nh_score)

    return CategoryScore(
        "Distribution", "👥",
        score=score,
        weight=weight,
        breakdown={
            "total_holders": total_holders,
            "th_score": th_score,
            "top10_concentration_pct": top10,
            "t10_score": t10_score,
            "holder_growth_24h_ratio": nh_ratio,
            "nh_score": nh_score,
        },
    )


def _holder_growth_24h(flows_resp: Any, total_holders: int | None) -> float | None:
    """flows の holders_count 推移から直近 24h の増加率を返す。

    flows.data は date ASC ソート。 末尾の最新値と 24h 前 (= 末尾の1個前) を比較。
    """
    if isinstance(flows_resp, BaseException) or not isinstance(flows_resp, dict):
        return None
    data = flows_resp.get("data")
    if not isinstance(data, list) or len(data) < 2:
        return None

    latest = data[-1] if isinstance(data[-1], dict) else None
    prev = data[-2] if isinstance(data[-2], dict) else None
    if latest is None or prev is None:
        return None

    latest_count = to_float(latest.get("holders_count"))
    prev_count = to_float(prev.get("holders_count"))
    if latest_count is None or prev_count is None or prev_count <= 0:
        return None

    new_in_24h = latest_count - prev_count
    base = float(total_holders) if total_holders else latest_count
    if base <= 0:
        return None
    return new_in_24h / base
