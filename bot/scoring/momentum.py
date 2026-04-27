"""Momentum カテゴリ (フェーズA簡略版)。

設計書: Buy/Sell比 + 買い手比 + 価格モメンタム + 出来高伸長 = 100点
フェーズA: 価格モメンタムと出来高伸長 (24h vs prev 24h) は別データなのでスキップ。
取得可能な 55 点を 100 点換算する。
"""
from __future__ import annotations

from typing import Any

from bot.scoring._helpers import get_dict, to_float, unwrap_data
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 12
RAW_MAX = 55  # bs(30) + buyer(25)


def calculate(token_info: Any, *, weight_total_pct: int) -> CategoryScore:
    WEIGHT = RAW_WEIGHT_PCT / weight_total_pct
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return CategoryScore("Momentum", "📈", 0.0, WEIGHT, note="データなし")

    data = unwrap_data(token_info)
    spot = get_dict(data, "spot_metrics")

    buy_vol = to_float(spot.get("buy_volume_usd"))
    sell_vol = to_float(spot.get("sell_volume_usd"))
    unique_buyers = to_float(spot.get("unique_buyers"))
    unique_sellers = to_float(spot.get("unique_sellers"))

    # Buy/Sell比 (max 30)
    bs_score = 0.0
    bs_ratio: float | None = None
    if buy_vol is not None and sell_vol is not None:
        bs_ratio = buy_vol / (sell_vol + 1)
        if bs_ratio >= 2.0:
            bs_score = 30
        elif bs_ratio >= 1.5:
            bs_score = 25
        elif bs_ratio >= 1.2:
            bs_score = 20
        elif bs_ratio >= 1.0:
            bs_score = 10
        else:
            bs_score = 0

    # 買い手比 (max 25)
    buyer_score = 0.0
    buyer_ratio: float | None = None
    if unique_buyers is not None and unique_sellers is not None:
        buyer_ratio = unique_buyers / (unique_sellers + 1)
        buyer_score = min(buyer_ratio / 2.0, 1.0) * 25

    raw = bs_score + buyer_score
    score = raw * 100 / RAW_MAX

    return CategoryScore(
        "Momentum", "📈", score, WEIGHT,
        breakdown={
            "buy_vol_usd": buy_vol,
            "sell_vol_usd": sell_vol,
            "buy_sell_ratio": bs_ratio,
            "unique_buyers": unique_buyers,
            "unique_sellers": unique_sellers,
            "buyer_ratio": buyer_ratio,
            "bs_score": bs_score,
            "buyer_score": buyer_score,
            "raw_max": RAW_MAX,
        },
    )
