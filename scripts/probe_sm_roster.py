"""Smart Money DEX Trades を 1 call で叩いて Helius 登録候補 roster を出す。

確定済み構成 (会話で合意):
- chains          : ["solana"]
- include labels  : ["Fund", "180D Smart Trader"]
- exclude labels  : ["30D Smart Trader"]
- token_bought_age: {min: 1, max: 30}      新興仕込み狙い
- trade_value_usd : {min: 5000}            少額ノイズ除外
- per_page        : 100  (1 call / 推定 5 credit)
- order           : block_timestamp DESC

paginate しない。 まず 1 call で取れる roster の感触を掴むのが目的。

Usage:
    .venv/bin/python -m scripts.probe_sm_roster
    .venv/bin/python -m scripts.probe_sm_roster --raw 2     # raw JSON を 2 件 dump
    .venv/bin/python -m scripts.probe_sm_roster --csv out.csv  # roster を CSV 出力
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
from collections import defaultdict
from typing import Any

from bot.config import Config
from bot.nansen_client import NansenClient

logger = logging.getLogger(__name__)


CONFIG: dict[str, Any] = {
    "chains": ["solana"],
    "include_labels": ["Fund", "180D Smart Trader"],
    "exclude_labels": ["30D Smart Trader"],
    "extra_filters": {
        "token_bought_age_days": {"min": 1, "max": 30},
        "trade_value_usd": {"min": 300},
    },
    "per_page": 100,
    "order_field": "block_timestamp",
    "order_direction": "DESC",
}


def _short(addr: str | None) -> str:
    if not isinstance(addr, str) or not addr:
        return "-"
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}…{addr[-4:]}"


def _aggregate_roster(data: list[dict]) -> list[dict]:
    """trader_address ごとに集計して Helius 登録 roster の素材を作る。"""
    by_wallet: dict[str, dict] = {}
    for r in data:
        if not isinstance(r, dict):
            continue
        w = r.get("trader_address")
        if not isinstance(w, str) or not w:
            continue
        slot = by_wallet.setdefault(
            w,
            {
                "wallet_address": w,
                "trade_count": 0,
                "sum_trade_value_usd": 0.0,
                "max_trade_value_usd": 0.0,
                "last_seen": "",
                "first_seen": "",
                "label": "",
                "bought_tokens": defaultdict(int),
                "sold_tokens": defaultdict(int),
            },
        )
        slot["trade_count"] += 1
        v = r.get("trade_value_usd")
        try:
            v_f = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            v_f = 0.0
        slot["sum_trade_value_usd"] += v_f
        if v_f > slot["max_trade_value_usd"]:
            slot["max_trade_value_usd"] = v_f
        ts = r.get("block_timestamp") or ""
        if not slot["last_seen"] or ts > slot["last_seen"]:
            slot["last_seen"] = ts
        if not slot["first_seen"] or ts < slot["first_seen"]:
            slot["first_seen"] = ts
        lbl = r.get("trader_address_label")
        if isinstance(lbl, str) and lbl and not slot["label"]:
            slot["label"] = lbl
        bs = r.get("token_bought_symbol")
        if isinstance(bs, str) and bs:
            slot["bought_tokens"][bs] += 1
        ss = r.get("token_sold_symbol")
        if isinstance(ss, str) and ss:
            slot["sold_tokens"][ss] += 1

    rows = list(by_wallet.values())
    rows.sort(key=lambda x: (x["trade_count"], x["sum_trade_value_usd"]), reverse=True)
    return rows


def _print_roster(rows: list[dict]) -> None:
    if not rows:
        print("\n(roster 該当なし)")
        return

    print(f"\n=== Helius 登録候補 roster: {len(rows)} wallet ===")
    header = (
        f"{'#':>3}  {'wallet':12}  {'cnt':>3}  {'sum_usd':>10}  {'max_usd':>10}  "
        f"{'last_seen':19}  {'label':25}  buy_tokens"
    )
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        bought = ",".join(
            f"{sym}×{n}"
            for sym, n in sorted(r["bought_tokens"].items(), key=lambda x: -x[1])[:3]
        )
        print(
            f"{i:>3}  {_short(r['wallet_address']):12}  "
            f"{r['trade_count']:>3}  "
            f"${r['sum_trade_value_usd']:>9,.0f}  "
            f"${r['max_trade_value_usd']:>9,.0f}  "
            f"{(r['last_seen'] or '-')[:19]:19}  "
            f"{(r['label'] or '-')[:25]:25}  "
            f"{bought}"
        )


def _write_csv(path: str, rows: list[dict]) -> None:
    fields = [
        "wallet_address",
        "trade_count",
        "sum_trade_value_usd",
        "max_trade_value_usd",
        "first_seen",
        "last_seen",
        "label",
        "bought_top",
        "sold_top",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            bought_top = ",".join(
                f"{sym}:{n}"
                for sym, n in sorted(r["bought_tokens"].items(), key=lambda x: -x[1])[:5]
            )
            sold_top = ",".join(
                f"{sym}:{n}"
                for sym, n in sorted(r["sold_tokens"].items(), key=lambda x: -x[1])[:5]
            )
            w.writerow(
                {
                    "wallet_address": r["wallet_address"],
                    "trade_count": r["trade_count"],
                    "sum_trade_value_usd": f"{r['sum_trade_value_usd']:.2f}",
                    "max_trade_value_usd": f"{r['max_trade_value_usd']:.2f}",
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "label": r["label"],
                    "bought_top": bought_top,
                    "sold_top": sold_top,
                }
            )
    print(f"\nCSV 書き出し: {path}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw", type=int, default=0, help="raw JSON を先頭から N 件 dump (default 0)"
    )
    parser.add_argument(
        "--csv", default=None, help="roster を CSV に出力するパス (省略時は出さない)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load()

    print("構成:")
    print(json.dumps(CONFIG, ensure_ascii=False, indent=2))

    async with NansenClient(cfg.nansen_api_key, cfg.nansen_base_url, chain="solana") as client:
        try:
            resp = await client.smart_money_dex_trades(
                chains=CONFIG["chains"],
                include_labels=CONFIG["include_labels"],
                exclude_labels=CONFIG["exclude_labels"],
                per_page=CONFIG["per_page"],
                order_field=CONFIG["order_field"],
                order_direction=CONFIG["order_direction"],
                extra_filters=CONFIG["extra_filters"],
            )
        except Exception as e:
            print(f"\nERROR: {e!r}")
            return
        print(f"\ncredit (推定): +{client.credits_used}")

    if not isinstance(resp, dict):
        print(f"非 dict レスポンス: {type(resp).__name__}")
        return
    print(f"top-level keys: {list(resp.keys())}")
    print(f"pagination    : {resp.get('pagination')}")

    data = resp.get("data") if isinstance(resp.get("data"), list) else []
    valid = [r for r in data if isinstance(r, dict)]
    print(f"records       : {len(valid)}")

    if args.raw > 0 and valid:
        for i in range(min(args.raw, len(valid))):
            print(f"\n--- data[{i}] raw ---")
            print(json.dumps(valid[i], indent=2, ensure_ascii=False, default=str))

    rows = _aggregate_roster(valid)
    _print_roster(rows)

    if args.csv:
        _write_csv(args.csv, rows)


if __name__ == "__main__":
    asyncio.run(main())
