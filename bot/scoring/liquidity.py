"""Liquidity カテゴリ (フル実装)。

設計書通り 流動性絶対額(50) + 出来高/流動性比(30) + MCap適正(20) = 100点
"""
from __future__ import annotations

from typing import Any

from bot.scoring._helpers import get_dict, to_float, unwrap_data
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 12


def calculate(token_info: Any, *, weight_total_pct: int) -> CategoryScore:
    WEIGHT = RAW_WEIGHT_PCT / weight_total_pct
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return CategoryScore("Liquidity", "💧", 0.0, WEIGHT, note="データなし")

    data = unwrap_data(token_info)
    spot = get_dict(data, "spot_metrics")
    td = get_dict(data, "token_details")

    liq = to_float(spot.get("liquidity_usd"))
    vol = to_float(spot.get("volume_total_usd"))
    mcap = to_float(td.get("market_cap_usd")) or to_float(td.get("fdv_usd"))

    # 流動性絶対額 (max 50)
    if liq is None:
        liq_score = 0.0
    elif liq >= 1_000_000:
        liq_score = 50
    elif liq >= 500_000:
        liq_score = 40
    elif liq >= 100_000:
        liq_score = 25
    elif liq >= 50_000:
        liq_score = 10
    else:
        liq_score = 0

    # 出来高/流動性比 (max 30)
    vlr: float | None = None
    if liq is None or vol is None or liq <= 0:
        vlr_score = 0.0
    else:
        vlr = vol / liq
        if 0.5 <= vlr <= 5.0:
            vlr_score = 30
        elif vlr <= 10.0:
            vlr_score = 20
        elif vlr > 10.0:
            vlr_score = 5
        else:
            vlr_score = 10

    # MCap適正 (max 20)
    if mcap is None:
        mc_score = 0.0
    elif mcap > 10_000_000:
        mc_score = 20
    elif mcap >= 1_000_000:
        mc_score = 15
    elif mcap >= 100_000:
        mc_score = 10
    else:
        mc_score = 5

    score = float(liq_score + vlr_score + mc_score)

    return CategoryScore(
        "Liquidity", "💧", score, WEIGHT,
        breakdown={
            "liquidity_usd": liq,
            "volume_usd": vol,
            "vol_liq_ratio": vlr,
            "market_cap_usd": mcap,
            "liq_score": liq_score,
            "vlr_score": vlr_score,
            "mc_score": mc_score,
        },
    )
