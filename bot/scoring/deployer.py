"""Deployer Trust カテゴリ。

設計書: 警告ラベル(40) + アカウント年齢(25) + 実績PnL(20) + 関連ウォレットクリーン度(15) = 100点

フェーズB MVP: 関連ウォレットの labels 個別取得は重いので一旦スキップ。
                rel_score は一律 15 (最大) として扱う。
                deployer 自身の labels / age / PnL は実装。

Solana の場合 deployer の特定は厳密ではなく、mintAuthority を deployer 候補とする。
mintAuthority が None (renounce 済み) のときは「安全シグナル」として warn_score 40 のみ計上。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.scoring._helpers import to_float
from bot.scoring.types import CategoryScore

RAW_WEIGHT_PCT = 8
DANGER_WORDS = ("scam", "hacker", "mixer", "sanctioned", "exploit", "drainer")
WARN_WORDS = ("suspicious", "phishing")


def calculate(
    *,
    deployer_address: str | None,
    deployer_fetched: bool,
    labels_resp: Any,
    transactions_resp: Any,
    pnl_resp: Any,
    creator_deploy_count: int | None,
    weight_total_pct: int,
) -> CategoryScore:
    """
    deployer_fetched: Solana RPC で mint info を取得できたか
        - False: RPC 失敗 → score 0、 N/A 表示
        - True かつ deployer_address=None: 真の renounce → 70点
        - True かつ deployer_address=str: 通常の deployer → Nansen profiler でフル評価
    """
    weight = RAW_WEIGHT_PCT / weight_total_pct

    if not deployer_fetched:
        return CategoryScore(
            "Deployer Trust", "🔍",
            score=0.0,
            weight=weight,
            note="Solana RPC からの mint authority 取得失敗 (スコア計上不可)",
            breakdown={
                "deployer_address": None,
                "fetched": False,
            },
        )

    if not deployer_address:
        # mintAuthority None → renounce 済み = 通常のラグプル抑止策として安全
        return CategoryScore(
            "Deployer Trust", "🔍",
            score=70.0,
            weight=weight,
            note="Mint Authority Renounced (deployer 不明・通常は安全シグナル)",
            breakdown={
                "deployer_address": None,
                "fetched": True,
                "renounced": True,
            },
        )

    # 警告ラベル (max 40)
    deployer_labels = _extract_labels(labels_resp)
    label_concat = " ".join(deployer_labels).lower()
    if any(w in label_concat for w in DANGER_WORDS):
        warn_score = 0
    elif any(w in label_concat for w in WARN_WORDS):
        warn_score = 10
    else:
        warn_score = 40

    # シリアルミーマー減点 (Helius creator deploy 数を見て上から差し引く)
    serial_penalty = _serial_penalty(creator_deploy_count)
    warn_score = max(0, warn_score - serial_penalty)

    # アカウント年齢 (max 25)
    days_active = _calc_days_active(transactions_resp)
    if days_active is None:
        age_score = 0
    elif days_active >= 365:
        age_score = 25
    elif days_active >= 180:
        age_score = 18
    elif days_active >= 90:
        age_score = 10
    elif days_active >= 30:
        age_score = 5
    else:
        age_score = 0

    # 実績 PnL (max 20)
    win_rate, total_trades = _calc_pnl(pnl_resp)
    if total_trades is None or total_trades < 5:
        pnl_score = 0
    elif win_rate is not None and win_rate >= 0.5 and total_trades >= 20:
        pnl_score = 20
    else:
        pnl_score = 10

    # 関連ウォレットクリーン度 (max 15)
    # フェーズB MVP: 個別 labels 未取得 → 一律 15 とする
    rel_score = 15

    score = float(warn_score + age_score + pnl_score + rel_score)

    return CategoryScore(
        "Deployer Trust", "🔍",
        score=score,
        weight=weight,
        breakdown={
            "deployer_address": deployer_address,
            "labels": deployer_labels,
            "warn_score": warn_score,
            "creator_deploy_count": creator_deploy_count,
            "serial_penalty": serial_penalty,
            "days_active": days_active,
            "age_score": age_score,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "pnl_score": pnl_score,
            "rel_score": rel_score,
        },
    )


def _serial_penalty(deploy_count: int | None) -> int:
    """creator が発行した token 数からシリアルミーマー減点を返す。"""
    if not isinstance(deploy_count, int):
        return 0
    if deploy_count >= 50:
        return 25
    if deploy_count >= 20:
        return 15
    if deploy_count >= 5:
        return 5
    return 0


def _extract_labels(labels_resp: Any) -> list[str]:
    if isinstance(labels_resp, BaseException) or not isinstance(labels_resp, dict):
        return []
    data = labels_resp.get("data")
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for x in data:
        if isinstance(x, dict):
            lbl = x.get("label")
            if isinstance(lbl, str):
                out.append(lbl)
    return out


def _calc_days_active(tx_resp: Any) -> int | None:
    if isinstance(tx_resp, BaseException) or not isinstance(tx_resp, dict):
        return None
    data = tx_resp.get("data")
    if not isinstance(data, list) or not data:
        return None
    oldest = data[0]
    if not isinstance(oldest, dict):
        return None
    ts = oldest.get("block_timestamp")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return delta.days


def _calc_pnl(pnl_resp: Any) -> tuple[float | None, int | None]:
    """(win_rate_ratio, total_trades) を返す。"""
    if isinstance(pnl_resp, BaseException) or not isinstance(pnl_resp, dict):
        return None, None
    body = pnl_resp.get("data") if isinstance(pnl_resp.get("data"), dict) else pnl_resp
    if not isinstance(body, dict):
        return None, None

    total_trades_raw = body.get("traded_times")
    win_rate_raw = to_float(body.get("win_rate"))

    try:
        total_trades = int(total_trades_raw) if total_trades_raw is not None else None
    except (TypeError, ValueError):
        total_trades = None

    if total_trades is None or total_trades <= 0 or win_rate_raw is None:
        return None, total_trades

    # ドキュメントによれば win_rate は「件数」 (勝ち回数)
    # 念のため: もし 0..1 の比率で来た場合に対応
    if 0 <= win_rate_raw <= 1 and win_rate_raw <= total_trades:
        # 0..1 の範囲は「比率」と解釈する余地あり。 ただし件数が小さいと判別困難。
        # 件数として扱うのが安全 (文書通り)。
        ratio = win_rate_raw / total_trades
    else:
        ratio = win_rate_raw / total_trades
    return ratio, total_trades
