"""SM 速報 BUY イベントを期間別 JSON にエクスポートする。

bot/wallet_db.py の sm_signal_events を読んで、 期間 (1h/6h/24h/7d) ごとに
集計 + buyers list を作って `<out_dir>/signals_<period>.json` に書き出す。
シンボル / mcap / price / image_url は DexScreener (token_info キャッシュ) で
補完する (--no-enrich で無効化可能)。

CLI 例:
    python -m dashboard.exporter.export_signals \
        --out ./dashboard_repo/data \
        --db data/wallets.db

bot cog からは `export_all()` を呼ぶ。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.token_info import get_token_infos
from bot.wallet_db import DEFAULT_DB_PATH, SCORE_CATEGORIES, WalletDB

logger = logging.getLogger(__name__)


def _scores_of(row: dict[str, Any]) -> dict[str, int]:
    """buyer/event 行から 5 カテゴリの累積スコアを {good,..,bot} で抽出。"""
    return {c: int(row.get(f"score_{c}") or 0) for c in SCORE_CATEGORIES}

# 期間 (label, hours)
DEFAULT_WINDOWS: list[tuple[str, int]] = [
    ("1h", 1),
    ("6h", 6),
    ("24h", 24),
    ("7d", 24 * 7),
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def _build_window_payload(
    db: WalletDB,
    *,
    window_label: str,
    window_hours: int,
    now_ts: int,
    min_distinct_buyers: int,
    top_n: int,
    buyers_per_token: int,
    events_per_token: int,
) -> dict[str, Any]:
    """1 期間ぶんの集計 dict を返す (JSON にそのまま dump 可能)。"""
    since_ts = now_ts - window_hours * 3600

    rows = await db.aggregate_sm_signals(
        since_block_ts=since_ts,
        min_distinct_buyers=min_distinct_buyers,
        limit=top_n,
    )
    total_events = await db.sm_signal_events_count(since_block_ts=since_ts)

    tokens: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        mint = d.get("target_mint")
        if not isinstance(mint, str):
            continue
        buyer_rows = await db.list_buyers_for_mint(
            target_mint=mint, since_block_ts=since_ts
        )
        buyers = [dict(b) for b in buyer_rows[:buyers_per_token]]
        event_rows = await db.list_events_for_mint(
            target_mint=mint, since_block_ts=since_ts, limit=events_per_token
        )
        tokens.append({
            "mint": mint,
            "distinct_buyers": int(d.get("distinct_buyers") or 0),
            "distinct_sellers": int(d.get("distinct_sellers") or 0),
            "buy_trades": int(d.get("buy_trades") or 0),
            "sell_trades": int(d.get("sell_trades") or 0),
            "sum_buy_sol": float(d.get("sum_buy_sol") or 0.0),
            "sum_buy_stable": float(d.get("sum_buy_stable") or 0.0),
            "sum_sell_sol": float(d.get("sum_sell_sol") or 0.0),
            "sum_sell_stable": float(d.get("sum_sell_stable") or 0.0),
            "n_large_buys": int(d.get("n_large_buys") or 0),
            "max_buy_quote": (
                float(d["max_buy_quote"]) if d.get("max_buy_quote") is not None else None
            ),
            "first_seen_ts": int(d.get("first_seen_ts") or 0),
            "last_seen_ts": int(d.get("last_seen_ts") or 0),
            "buyers": [
                {
                    "wallet": b.get("wallet"),
                    "label": b.get("label"),
                    "scores": _scores_of(b),
                    "trades": int(b.get("trades") or 0),
                    "sum_sol": float(b.get("sum_sol") or 0.0),
                    "sum_stable": float(b.get("sum_stable") or 0.0),
                    "last_ts": int(b.get("last_ts") or 0),
                }
                for b in buyers
            ],
            "events": [
                {
                    "ts": int(e.get("block_ts") or 0),
                    "wallet": e.get("wallet"),
                    "label": e.get("label"),
                    "scores": _scores_of(e),
                    "direction": e.get("direction"),
                    "quote_label": e.get("quote_label"),
                    "quote_change": float(e.get("quote_change") or 0.0),
                    "target_change": float(e.get("target_change") or 0.0),
                    "is_large": bool(e.get("is_large")),
                    "signature": e.get("signature"),
                }
                for e in (dict(x) for x in event_rows)
            ],
        })

    return {
        "window": window_label,
        "window_hours": window_hours,
        "since_ts": since_ts,
        "now_ts": now_ts,
        "generated_at": _utcnow_iso(),
        "total_events_in_window": int(total_events),
        "min_distinct_buyers": int(min_distinct_buyers),
        "tokens": tokens,
    }


async def _enrich_tokens(payloads: list[dict[str, Any]]) -> None:
    """DexScreener (token_info キャッシュ) で symbol / name / mcap / image を補完。

    全 window をまたいで unique な mint だけを並列 fetch して in-place 更新する。
    fetch 失敗は無視 (既存値があればそのまま)。
    """
    addrs: set[str] = set()
    for p in payloads:
        for t in p.get("tokens", []):
            mint = t.get("mint")
            if isinstance(mint, str):
                addrs.add(mint)
    if not addrs:
        return

    try:
        infos = await get_token_infos(sorted(addrs))
    except Exception:
        logger.warning("export_signals: DexScreener 補完で例外", exc_info=True)
        return

    for p in payloads:
        for t in p.get("tokens", []):
            info = infos.get(t.get("mint"))
            if info is None:
                continue
            if info.symbol:
                t["symbol"] = info.symbol
            if info.name:
                t["name"] = info.name
            if info.market_cap is not None:
                t["market_cap"] = info.market_cap
            if info.price_usd is not None:
                t["price_usd"] = info.price_usd
            if info.image_url:
                t["image_url"] = info.image_url
            if info.liquidity_usd is not None:
                t["liquidity_usd"] = info.liquidity_usd
            if info.volume:
                t["volume"] = info.volume
            if info.txns:
                t["txns"] = info.txns
            if info.price_change:
                t["price_change"] = info.price_change
            if info.pair_created_at_ms is not None:
                t["pair_created_at_ms"] = info.pair_created_at_ms


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    # 末尾改行を付ける (git diff が綺麗になる)
    path.write_text(text + "\n", encoding="utf-8")


async def export_all(
    *,
    out_dir: str | Path,
    db_path: str = DEFAULT_DB_PATH,
    windows: list[tuple[str, int]] | None = None,
    min_distinct_buyers: int = 2,
    top_n: int = 50,
    buyers_per_token: int = 30,
    events_per_token: int = 200,
    enrich: bool = True,
) -> dict[str, int]:
    """全 window を集計して JSON 出力。 各 window の token 件数を dict で返す。"""
    out = Path(out_dir)
    windows = windows or DEFAULT_WINDOWS
    now_ts = int(time.time())

    payloads: list[dict[str, Any]] = []
    async with WalletDB(db_path) as db:
        for label, hours in windows:
            payload = await _build_window_payload(
                db,
                window_label=label,
                window_hours=hours,
                now_ts=now_ts,
                min_distinct_buyers=min_distinct_buyers,
                top_n=top_n,
                buyers_per_token=buyers_per_token,
                events_per_token=events_per_token,
            )
            payloads.append(payload)

    if enrich:
        await _enrich_tokens(payloads)

    counts: dict[str, int] = {}
    for payload in payloads:
        label = payload["window"]
        _write_json(out / f"signals_{label}.json", payload)
        counts[label] = len(payload["tokens"])

    meta = {
        "generated_at": _utcnow_iso(),
        "now_ts": now_ts,
        "windows": [w[0] for w in windows],
        "min_distinct_buyers": min_distinct_buyers,
        "top_n": top_n,
        "counts": counts,
    }
    _write_json(out / "meta.json", meta)
    return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SM 速報 BUY を期間別 JSON に書き出す")
    parser.add_argument("--out", required=True, help="出力ディレクトリ (data/ 等)")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--min-buyers", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--buyers-per-token", type=int, default=30)
    parser.add_argument("--events-per-token", type=int, default=200,
                        help="銘柄ごとに出力する時系列イベントの最大件数")
    parser.add_argument("--no-enrich", action="store_true", help="DexScreener 補完をしない")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    counts = asyncio.run(
        export_all(
            out_dir=args.out,
            db_path=args.db,
            min_distinct_buyers=args.min_buyers,
            top_n=args.top_n,
            buyers_per_token=args.buyers_per_token,
            events_per_token=args.events_per_token,
            enrich=not args.no_enrich,
        )
    )
    logger.info("export 完了: %s", counts)


if __name__ == "__main__":
    main()
