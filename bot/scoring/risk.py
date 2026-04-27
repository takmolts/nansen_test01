"""Risk カテゴリ。 高い = 安全。

設計書:
- BTC 連動リスク (max 30) ← nansen-indicators の btc-reflexivity の score
- CEX 流入リスク (max 25) ← flow-intelligence の exchange_net_flow_usd / volume
- トークン年齢 (max 20) ← token-info の token_deployment_date
- Nansen 独自リスク指標 (max 25) ← liquidity-risk + concentration-risk + token-supply-inflation
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.scoring._helpers import get_dict, to_float, unwrap_data
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 12

# nansen-indicators の risk_indicators の indicator_type
TYPE_BTC_REFLEXIVITY = "btc-reflexivity"
TYPE_LIQUIDITY = "liquidity-risk"
TYPE_CONCENTRATION = "concentration-risk"
TYPE_INFLATION = "token-supply-inflation"


def calculate(
    *,
    token_info: Any,
    nansen_indicators_resp: Any,
    flow_intelligence_resp: Any,
    weight_total_pct: int,
) -> CategoryScore:
    weight = RAW_WEIGHT_PCT / weight_total_pct

    # --- BTC 連動 (max 30) ---
    indicators_map = _extract_indicators(nansen_indicators_resp)
    btc_score, btc_signal_label = _score_btc_reflexivity(indicators_map.get(TYPE_BTC_REFLEXIVITY))

    # --- CEX 流入 (max 25) ---
    cex_score, cex_ratio = _score_cex_inflow(flow_intelligence_resp, token_info)

    # --- 年齢 (max 20) ---
    age_score, age_days = _score_age(token_info)

    # --- Nansen 独自リスク指標 (max 25) ---
    ni_score, ni_summary = _score_nansen_indicators(indicators_map)

    score = float(btc_score + cex_score + age_score + ni_score)

    return CategoryScore(
        "Risk", "🛡️",
        score=score,
        weight=weight,
        breakdown={
            "btc_signal": btc_signal_label,
            "btc_score": btc_score,
            "cex_inflow_ratio": cex_ratio,
            "cex_score": cex_score,
            "age_days": age_days,
            "age_score": age_score,
            "ni_summary": ni_summary,
            "ni_score": ni_score,
        },
    )


def _extract_indicators(resp: Any) -> dict[str, dict]:
    """indicator_type → indicator dict のマップを返す。"""
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return {}
    arr = resp.get("risk_indicators")
    if not isinstance(arr, list):
        return {}
    out: dict[str, dict] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        t = item.get("indicator_type")
        if isinstance(t, str):
            out[t] = item
    return out


def _score_btc_reflexivity(indicator: Any) -> tuple[float, str | None]:
    if not isinstance(indicator, dict):
        return 0.0, None
    label = str(indicator.get("score", "")).lower()
    if label == "low":
        return 30.0, label
    if label == "medium":
        return 20.0, label
    if label == "high":
        return 5.0, label
    return 0.0, label or None


def _score_cex_inflow(flow_resp: Any, token_info: Any) -> tuple[float, float | None]:
    """exchange_net_flow_usd / volume_total_usd で売り圧比率を測る。

    net_flow が正 (CEX に流入 = 売り圧懸念) のときのみ減点。
    マイナス (CEX から流出 = 買い圧) なら満点。
    """
    if isinstance(flow_resp, BaseException) or not isinstance(flow_resp, dict):
        return 0.0, None

    data = flow_resp.get("data")
    if not isinstance(data, list) or not data:
        return 0.0, None

    latest = data[0]
    if not isinstance(latest, dict):
        return 0.0, None

    net_flow = to_float(latest.get("exchange_net_flow_usd"))
    if net_flow is None:
        return 0.0, None

    # token-info から volume_total_usd
    spot = get_dict(unwrap_data(token_info), "spot_metrics")
    volume = to_float(spot.get("volume_total_usd"))
    if volume is None or volume <= 0:
        return 0.0, None

    if net_flow <= 0:
        return 25.0, 0.0

    ratio = net_flow / volume
    if ratio <= 0.05:
        return 25.0, ratio
    if ratio <= 0.15:
        return 15.0, ratio
    if ratio <= 0.30:
        return 5.0, ratio
    return 0.0, ratio


def _score_age(token_info: Any) -> tuple[float, int | None]:
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return 0.0, None
    td = get_dict(unwrap_data(token_info), "token_details")
    deployment = td.get("token_deployment_date")
    if not isinstance(deployment, str) or not deployment:
        return 0.0, None
    try:
        dt = datetime.fromisoformat(deployment.replace("Z", "+00:00"))
    except ValueError:
        return 0.0, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days

    if days >= 180:
        return 20.0, days
    if days >= 90:
        return 15.0, days
    if days >= 30:
        return 10.0, days
    if days >= 7:
        return 5.0, days
    return 0.0, days


def _score_nansen_indicators(indicators_map: dict[str, dict]) -> tuple[float, dict]:
    """liquidity-risk / concentration-risk / token-supply-inflation の集約スコア。"""
    targets = [TYPE_LIQUIDITY, TYPE_CONCENTRATION, TYPE_INFLATION]
    summary: dict[str, str | None] = {}
    high_count = 0
    medium_count = 0
    found = 0
    for t in targets:
        ind = indicators_map.get(t)
        if not isinstance(ind, dict):
            summary[t] = None
            continue
        label = str(ind.get("score", "")).lower()
        summary[t] = label or None
        if label == "high":
            high_count += 1
            found += 1
        elif label == "medium":
            medium_count += 1
            found += 1
        elif label == "low":
            found += 1

    if found == 0:
        return 0.0, summary

    base = 25.0
    score = max(0.0, base - high_count * 10 - medium_count * 5)
    return score, summary
