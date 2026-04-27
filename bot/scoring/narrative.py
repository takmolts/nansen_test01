"""Narrative カテゴリ (αβ判定 + Trending + Boost + Social)。

設計書: 類似トークン数(40) + α/β詳細(25) + Trending/Boost(25) + Social(10) = 100
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.scoring._helpers import get_dict, unwrap_data
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 15

SIMILAR_DAYS = 7  # 「最近 N 日以内に出た類似トークン」の判定窓


def calculate(
    *,
    token_info: Any,
    token_address: str,
    similar_pairs: list[dict[str, Any]] | None,
    is_dexscreener_boosted: bool | None,
    is_coingecko_trending: bool | None,
    weight_total_pct: int,
) -> CategoryScore:
    weight = RAW_WEIGHT_PCT / weight_total_pct

    self_deploy = _self_deploy_dt(token_info)
    similar_tokens = _extract_similar_tokens(
        similar_pairs or [],
        self_address=token_address,
    )
    similar_recent = [t["deploy_dt"] for t in similar_tokens]

    is_oldest = _is_oldest(self_deploy, similar_recent)
    is_early_half = _is_early_half(self_deploy, similar_recent)

    # 類似トークン数 (max 40, αβ 判定)
    n = len(similar_recent)
    if n == 0:
        sim_score = 0.0
        status = "isolated"
    elif n <= 3 and is_oldest:
        sim_score = 40.0
        status = "α"
    elif n <= 3:
        sim_score = 20.0
        status = "β"
    elif n <= 10:
        sim_score = 30.0
        status = "hot"
    else:
        sim_score = 15.0
        status = "saturated"

    # α/β 詳細 (max 25)
    if is_oldest:
        elder_score = 25.0
    elif is_early_half:
        elder_score = 15.0
    else:
        elder_score = 5.0

    # Trending / Boost (max 25)
    bs = is_dexscreener_boosted is True
    cg = is_coingecko_trending is True
    if bs and cg:
        trend_score = 25.0
    elif bs or cg:
        trend_score = 20.0
    else:
        trend_score = 0.0

    # ソーシャル充実度 (max 10)
    social_count = _count_socials(token_info)
    social_score = {3: 10.0, 2: 6.0, 1: 3.0, 0: 0.0}.get(social_count, 0.0)

    score = sim_score + elder_score + trend_score + social_score

    return CategoryScore(
        "Narrative", "🌊",
        score=float(score),
        weight=weight,
        breakdown={
            "similar_recent_count": n,
            "similar_tokens": similar_tokens,
            "status": status,
            "sim_score": sim_score,
            "is_oldest": is_oldest,
            "is_early_half": is_early_half,
            "elder_score": elder_score,
            "is_dexscreener_boosted": is_dexscreener_boosted,
            "is_coingecko_trending": is_coingecko_trending,
            "trend_score": trend_score,
            "social_count": social_count,
            "social_score": social_score,
        },
    )


def _self_deploy_dt(token_info: Any) -> datetime | None:
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return None
    td = get_dict(unwrap_data(token_info), "token_details")
    s = td.get("token_deployment_date")
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_similar_tokens(
    pairs: list[dict[str, Any]],
    *,
    self_address: str,
) -> list[dict[str, Any]]:
    """直近 SIMILAR_DAYS 以内の類似トークン情報を返す (deploy 日時昇順)。

    自トークン自身は除外。 同じトークンが複数 pair に存在する場合は最古の pairCreatedAt を採用。
    各要素は {address, symbol, name, deploy_dt} の dict。
    """
    self_lower = self_address.lower()
    by_token: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    threshold = now.timestamp() - SIMILAR_DAYS * 24 * 3600

    for p in pairs:
        base = p.get("baseToken") if isinstance(p.get("baseToken"), dict) else None
        if not isinstance(base, dict):
            continue
        addr = base.get("address")
        if not isinstance(addr, str) or addr.lower() == self_lower:
            continue
        created = p.get("pairCreatedAt")
        if not isinstance(created, (int, float)):
            continue
        ts_sec = created / 1000.0
        if ts_sec < threshold:
            continue
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        key = addr.lower()
        existing = by_token.get(key)
        if existing is None or dt < existing["deploy_dt"]:
            by_token[key] = {
                "address": addr,
                "symbol": str(base.get("symbol") or ""),
                "name": str(base.get("name") or ""),
                "deploy_dt": dt,
            }

    return sorted(by_token.values(), key=lambda x: x["deploy_dt"])


def _is_oldest(self_deploy: datetime | None, similar_recent: list[datetime]) -> bool:
    if self_deploy is None:
        return False
    if not similar_recent:
        return True
    return all(self_deploy <= d for d in similar_recent)


def _is_early_half(self_deploy: datetime | None, similar_recent: list[datetime]) -> bool:
    if self_deploy is None or not similar_recent:
        return False
    older_count = sum(1 for d in similar_recent if d < self_deploy)
    return older_count < len(similar_recent) / 2


def _count_socials(token_info: Any) -> int:
    if isinstance(token_info, BaseException) or not isinstance(token_info, dict):
        return 0
    td = get_dict(unwrap_data(token_info), "token_details")
    count = 0
    for k in ("website", "x", "telegram"):
        v = td.get(k)
        if isinstance(v, str) and v.startswith("http"):
            count += 1
    return count
