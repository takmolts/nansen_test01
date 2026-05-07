"""Smart Money DEX Trades エンドポイントの実 API レスポンス観測 probe。

複数の include/exclude ラベル組合せで叩いて、件数 / ユニークウォレット数 /
ラベル分布 / chain / 上位 buy token / credit 消費 をまとめて表示する。

DB には何も書かない。 まず API の手応えを掴むためのもの。

Usage:
    .venv/bin/python -m scripts.probe_smart_money
    .venv/bin/python -m scripts.probe_smart_money --chain solana --per-page 1000
    .venv/bin/python -m scripts.probe_smart_money --chain all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from typing import Any

from bot.config import Config
from bot.nansen_client import NansenClient

logger = logging.getLogger(__name__)

ALL_LABELS = [
    "Fund",
    "Smart Trader",
    "30D Smart Trader",
    "90D Smart Trader",
    "180D Smart Trader",
    "Smart HL Perps Trader",
]

# Helius 登録対象を絞る判断材料を取りたいので、 ノイズ多寡で 3 パターン比較する。
PROBE_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "ALL  (全ラベル)",
        "include_labels": ALL_LABELS,
        "exclude_labels": None,
    },
    {
        "name": "EX-30D  (新興枠 30D Smart Trader を除外)",
        "include_labels": None,
        "exclude_labels": ["30D Smart Trader"],
    },
    {
        "name": "HIGH-PURITY  (Fund + Smart Trader + 180D)",
        "include_labels": ["Fund", "Smart Trader", "180D Smart Trader"],
        "exclude_labels": None,
    },
]


def _summarise(name: str, data: list[dict], cost: int) -> None:
    print(f"\n=== {name} ===")
    print(f"  records       : {len(data)}")
    print(f"  credit (推定) : +{cost}")

    wallets = [r.get("trader_address") for r in data if isinstance(r, dict)]
    unique_wallets = {w for w in wallets if isinstance(w, str) and w}
    print(f"  unique wallet : {len(unique_wallets)}")

    label_counter: Counter[str] = Counter()
    for r in data:
        if not isinstance(r, dict):
            continue
        lbl = r.get("trader_address_label") or "(no label)"
        label_counter[lbl] += 1
    print("  label 分布 (上位 15):")
    for lbl, cnt in label_counter.most_common(15):
        print(f"    - {lbl:35s} : {cnt}")

    chain_counter = Counter(
        r.get("chain") for r in data if isinstance(r, dict) and r.get("chain")
    )
    print(f"  chain 分布   : {dict(chain_counter)}")

    bought_counter: Counter[str] = Counter()
    for r in data:
        if not isinstance(r, dict):
            continue
        sym = r.get("token_bought_symbol")
        if isinstance(sym, str) and sym:
            bought_counter[sym] += 1
    print("  buy 上位 10:")
    for sym, cnt in bought_counter.most_common(10):
        print(f"    - {sym:15s} : {cnt}")

    # ウォレット x ラベル のユニーク組: Helius 登録対象として実際に何件になるか
    label_by_wallet: dict[str, set[str]] = {}
    for r in data:
        if not isinstance(r, dict):
            continue
        w = r.get("trader_address")
        lbl = r.get("trader_address_label")
        if isinstance(w, str) and w and isinstance(lbl, str) and lbl:
            label_by_wallet.setdefault(w, set()).add(lbl)
    multi_label = sum(1 for s in label_by_wallet.values() if len(s) > 1)
    print(f"  複数ラベル持ち wallet : {multi_label}")


def _dump_raw(name: str, resp: dict, data: list[dict], n_samples: int) -> None:
    """生レスポンスのスキーマを観察するためのダンプ。

    - レスポンス top-level の key 一覧 (data 以外にメタが入っている可能性)
    - data[0..n_samples-1] の全フィールドを raw JSON で出力
    - data 全体に出現する key の和集合 (1 件目に無い optional フィールドの捕捉)
    """
    print(f"\n--- RAW DUMP ({name}) ---")
    print(f"  top-level keys : {list(resp.keys())}")
    for k, v in resp.items():
        if k == "data":
            continue
        # data 以外の値は metadata の可能性大なので浅く dump
        try:
            preview = json.dumps(v, ensure_ascii=False, default=str)[:300]
        except Exception:
            preview = repr(v)[:300]
        print(f"  resp[{k!r}] = {preview}")

    if data:
        all_keys: set[str] = set()
        for r in data:
            if isinstance(r, dict):
                all_keys.update(r.keys())
        print(f"  data[*] フィールド和集合 ({len(all_keys)} keys): {sorted(all_keys)}")

        for i in range(min(n_samples, len(data))):
            row = data[i]
            print(f"\n  --- data[{i}] raw JSON ---")
            print(json.dumps(row, indent=2, ensure_ascii=False, default=str))


async def _run_one(
    client: NansenClient,
    *,
    name: str,
    chains: list[str] | None,
    include_labels: list[str] | None,
    exclude_labels: list[str] | None,
    per_page: int,
    raw_samples: int,
) -> None:
    before = client.credits_used
    try:
        resp = await client.smart_money_dex_trades(
            chains=chains,
            include_labels=include_labels,
            exclude_labels=exclude_labels,
            per_page=per_page,
        )
    except Exception as e:
        print(f"\n=== {name} ===")
        print(f"  ERROR: {e!r}")
        return
    cost = client.credits_used - before

    if not isinstance(resp, dict):
        print(f"\n=== {name} ===")
        print(f"  非 dict レスポンス: {type(resp).__name__}")
        return
    data = resp.get("data")
    if not isinstance(data, list):
        print(f"\n=== {name} ===")
        print(f"  data フィールド不在 / resp keys = {list(resp.keys())}")
        return
    _summarise(name, data, cost)
    if raw_samples > 0:
        _dump_raw(name, resp, [r for r in data if isinstance(r, dict)], raw_samples)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain", default="solana", help="solana / ethereum / all など。 デフォは solana")
    parser.add_argument("--per-page", type=int, default=1000, help="ページ件数 (max 1000)")
    parser.add_argument(
        "--raw-samples",
        type=int,
        default=2,
        help="各構成について raw JSON を何件 dump するか (0=ダンプしない)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load()

    chains = [args.chain] if args.chain else None
    print(f"chains = {chains} / per_page = {args.per_page}")

    async with NansenClient(cfg.nansen_api_key, cfg.nansen_base_url, chain=args.chain) as client:
        for c in PROBE_CONFIGS:
            await _run_one(
                client,
                name=c["name"],
                chains=chains,
                include_labels=c["include_labels"],
                exclude_labels=c["exclude_labels"],
                per_page=args.per_page,
                raw_samples=args.raw_samples,
            )
        print(f"\n=== TOTAL credits used (推定) : {client.credits_used} ===")


if __name__ == "__main__":
    asyncio.run(main())
