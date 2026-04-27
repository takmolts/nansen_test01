"""scoring 用の小物ヘルパ (循環依存回避のため embeds とは独立)."""
from __future__ import annotations

from typing import Any


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_dict(d: Any, key: str) -> dict[str, Any]:
    """d.get(key) が dict ならそれを返す、無ければ空 dict。"""
    if not isinstance(d, dict):
        return {}
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def unwrap_data(token_info: Any) -> dict[str, Any]:
    """token-information レスポンスから内側 data dict を取り出す。失敗時は空 dict。"""
    if not isinstance(token_info, dict):
        return {}
    data = token_info.get("data")
    if isinstance(data, dict):
        return data
    return token_info
