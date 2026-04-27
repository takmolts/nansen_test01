"""Smart Money カテゴリ (フェーズA簡略版)。

設計書本来は SM保有数 + ネットフロー + 新規買い数 の 3 軸だが、
フェーズAでは who-bought-sold (BUY) のみで「買い件数 + 買い額」の 2 軸で算出する。
"""
from __future__ import annotations

from typing import Any

from bot.scoring._helpers import to_float
from bot.scoring.types import CategoryScore

# 元の SM 20% を 65% 中で正規化 → 20/65
WEIGHT = 20 / 65


def calculate(who_bought_sold: Any) -> CategoryScore:
    if isinstance(who_bought_sold, BaseException) or who_bought_sold is None:
        return CategoryScore(
            name="Smart Money", emoji="🧠",
            score=0.0, weight=WEIGHT, note="データ取得失敗",
        )

    items: list[dict] = []
    if isinstance(who_bought_sold, dict):
        raw = who_bought_sold.get("data")
        if isinstance(raw, list):
            items = [x for x in raw if isinstance(x, dict)]

    buy_count = len(items)
    buy_amount = 0.0
    for it in items:
        v = to_float(it.get("bought_volume_usd")) \
            or to_float(it.get("trade_volume_usd")) \
            or to_float(it.get("volume_usd"))
        if v:
            buy_amount += v

    # 設計書: 新規買い 24h 10人で満点25 / フローは別軸だがここでは買い額で代用
    count_score = min(buy_count / 10.0, 1.0) * 50.0
    amount_score = min(buy_amount / 500_000.0, 1.0) * 50.0
    score = count_score + amount_score  # max 100

    return CategoryScore(
        name="Smart Money", emoji="🧠",
        score=score, weight=WEIGHT,
        breakdown={
            "buy_count": buy_count,
            "buy_amount_usd": buy_amount,
            "count_score": count_score,
            "amount_score": amount_score,
        },
    )
