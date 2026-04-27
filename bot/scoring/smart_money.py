"""Smart Money カテゴリ (フェーズB3 完成版)。

設計書通り 3 軸構成:
- SM 保有ウォレット数 (max 40) ← /tgm/holders --smart-money
- SM ネットフロー (max 35, ±35) ← flow-intelligence の smart_trader_net_flow_usd
- SM 新規買い 24h (max 25) ← /tgm/who-bought-sold (件数)
"""
from __future__ import annotations

from typing import Any

from bot.scoring._helpers import to_float
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 20


def calculate(
    *,
    sm_holders_resp: Any,
    flow_intelligence_resp: Any,
    who_bought_sold_resp: Any,
    weight_total_pct: int,
) -> CategoryScore:
    weight = RAW_WEIGHT_PCT / weight_total_pct

    # SM 保有ウォレット数 (max 40)
    sm_holder_count = _count_sm_holders(sm_holders_resp)
    sm_holder_score = min(sm_holder_count / 20.0, 1.0) * 40 if sm_holder_count is not None else 0.0

    # ネットフロー (±35) ← flow-intelligence の smart_trader_net_flow_usd
    netflow = _smart_trader_net_flow(flow_intelligence_resp)
    if netflow is None:
        sm_flow_score = 0.0
    elif netflow >= 0:
        sm_flow_score = min(netflow / 500_000.0, 1.0) * 35
    else:
        sm_flow_score = max(-35.0, netflow / 500_000.0 * 35)

    # 新規買い 24h (max 25) ← who-bought-sold の件数
    new_buyers = _new_buyers_count(who_bought_sold_resp)
    sm_new_buyer_score = min(new_buyers / 10.0, 1.0) * 25 if new_buyers is not None else 0.0

    raw = sm_holder_score + sm_flow_score + sm_new_buyer_score
    score = max(0.0, min(100.0, raw))

    return CategoryScore(
        "Smart Money", "🧠",
        score=float(score),
        weight=weight,
        breakdown={
            "sm_holder_count": sm_holder_count,
            "sm_holder_score": sm_holder_score,
            "smart_trader_net_flow_usd": netflow,
            "sm_flow_score": sm_flow_score,
            "new_buyers_count": new_buyers,
            "sm_new_buyer_score": sm_new_buyer_score,
        },
    )


def _count_sm_holders(resp: Any) -> int | None:
    """holders --smart-money レスポンスから SM ホルダー数 (上位 per_page 内) を返す。"""
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return None
    data = resp.get("data")
    if not isinstance(data, list):
        return None
    return sum(1 for h in data if isinstance(h, dict))


def _smart_trader_net_flow(resp: Any) -> float | None:
    """flow-intelligence レスポンスから smart_trader_net_flow_usd を返す。"""
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return None
    data = resp.get("data")
    if not isinstance(data, list) or not data:
        return None
    latest = data[0]
    if not isinstance(latest, dict):
        return None
    return to_float(latest.get("smart_trader_net_flow_usd"))


def _new_buyers_count(resp: Any) -> int | None:
    """who-bought-sold レスポンスのエントリ数 (=直近 SM 買い件数) を返す。"""
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return None
    data = resp.get("data")
    if not isinstance(data, list):
        return None
    return sum(1 for x in data if isinstance(x, dict))
