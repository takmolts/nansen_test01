"""スコアリング結果を表すデータクラス群。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CategoryScore:
    """1カテゴリ分のスコア。"""
    name: str                                # 表示名 (例 "Smart Money")
    emoji: str                               # 表示用絵文字
    score: float                             # 0..100
    weight: float                            # 0..1 (フェーズA再正規化済み)
    breakdown: dict[str, Any] = field(default_factory=dict)
    note: str = ""                           # 補足コメント(任意)


@dataclass(frozen=True)
class TotalScore:
    """総合スコア + カテゴリ別スコア一覧。"""
    categories: list[CategoryScore]
    total: float

    @property
    def band(self) -> str:
        if self.total >= 80:
            return "STRONG BUY"
        if self.total >= 60:
            return "BUY"
        if self.total >= 40:
            return "CAUTION"
        return "AVOID"

    @property
    def band_emoji(self) -> str:
        return {
            "STRONG BUY": "🟢🟢",
            "BUY": "🟢",
            "CAUTION": "🟡",
            "AVOID": "🔴",
        }[self.band]
