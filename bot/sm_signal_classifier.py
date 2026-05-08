"""Helius enhanced webhook の event を SM wallet 視点で SWAP 分類する純関数群。

bb_bot の classify_transaction が抱えていた以下のバグを解消する:
1. accountData の filter を `account == wallet` でかけてしまい SPL 残高変化を取り逃す
   → 全 accountData を走査し、 tokenBalanceChanges[].userAccount == wallet で拾う
2. rawTokenAmount.tokenAmount を「最終残高」と誤認してスキップ
   → 実際は signed delta (符号付き string)。 decimals で割って human 単位に
3. 異 mint × 異 decimals を単純合算
   → mint ごと別 dict で持つ
4. USDC と wSOL を mid_total で合算 → quote が判別不能
   → quote は mint 単位で別々に保持し、 stable 優先 / 符号反転で自動判別

副産物として wSOL 残高変化を SOL に統合する (SOL_KEY = "__SOL__")。
"""
from __future__ import annotations

from typing import Any

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
USD1_MINT = "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB"

STABLE_LABELS: dict[str, str] = {
    USDC_MINT: "USDC",
    USDT_MINT: "USDT",
    USD1_MINT: "USD1",
}

# net dict 内で SOL (native + wSOL 統合) を表す仮想キー
SOL_KEY = "__SOL__"


def collect_involved_wallets(event: dict[str, Any]) -> set[str]:
    """event から関与 wallet (account / userAccount / native fromUserAccount/toUserAccount) を集める。

    SM roster との突合せに使う。 account は token account を含むので最終的に
    SM wallet 集合との intersection を取って絞り込む。
    """
    out: set[str] = set()
    for acc in event.get("accountData") or []:
        if not isinstance(acc, dict):
            continue
        a = acc.get("account")
        if isinstance(a, str) and a:
            out.add(a)
        for tbc in acc.get("tokenBalanceChanges") or []:
            if not isinstance(tbc, dict):
                continue
            ua = tbc.get("userAccount")
            if isinstance(ua, str) and ua:
                out.add(ua)
    for nt in event.get("nativeTransfers") or []:
        if not isinstance(nt, dict):
            continue
        for k in ("fromUserAccount", "toUserAccount"):
            v = nt.get(k)
            if isinstance(v, str) and v:
                out.add(v)
    for tt in event.get("tokenTransfers") or []:
        if not isinstance(tt, dict):
            continue
        for k in ("fromUserAccount", "toUserAccount"):
            v = tt.get(k)
            if isinstance(v, str) and v:
                out.add(v)
    return out


def wallet_net_by_mint(event: dict[str, Any], wallet: str) -> dict[str, float]:
    """target wallet 視点の mint→change を返す (decimals 適用済 / wSOL→SOL 統合済)。

    - 全 accountData を走査
    - tokenBalanceChanges[].userAccount == wallet をすべて取り込み (account 側で
      filter しない)
    - rawTokenAmount.tokenAmount は signed string、 decimals で割って human 単位
    - account == wallet の枝の nativeBalanceChange を SOL に積算
    - wSOL の change は SOL に統合 (仮想キー __SOL__)
    """
    net: dict[str, float] = {}
    sol_lamports = 0
    for acc in event.get("accountData") or []:
        if not isinstance(acc, dict):
            continue
        if acc.get("account") == wallet:
            try:
                sol_lamports += int(acc.get("nativeBalanceChange") or 0)
            except (TypeError, ValueError):
                pass
        for tbc in acc.get("tokenBalanceChanges") or []:
            if not isinstance(tbc, dict):
                continue
            if tbc.get("userAccount") != wallet:
                continue
            mint = tbc.get("mint")
            if not isinstance(mint, str) or not mint:
                continue
            raw = tbc.get("rawTokenAmount") or {}
            try:
                amt_str = raw.get("tokenAmount")
                dec = raw.get("decimals")
                if amt_str is None or dec is None:
                    continue
                change = int(amt_str) / (10 ** int(dec))
            except (TypeError, ValueError):
                continue
            net[mint] = net.get(mint, 0.0) + change
    # wSOL → SOL 統合
    if WSOL_MINT in net:
        sol_lamports += int(round(net.pop(WSOL_MINT) * 1e9))
    if sol_lamports != 0:
        net[SOL_KEY] = sol_lamports / 1e9
    return net


def classify_swap(net: dict[str, float]) -> dict[str, Any] | None:
    """net dict から swap 分類結果を返す。 該当しなければ None。

    返り値 dict:
        target_mint     : str   非 quote の主要 mint (CA)
        target_change   : float decimals 適用済 (符号付き)
        quote_label     : str   "USDC" / "USDT" / "USD1" / "SOL"
        quote_mint      : str   quote の mint (SOL の場合は WSOL_MINT。 SOL_KEY ではなく
                                CA を返す方が後段で扱いやすいため)
        quote_change    : float 同上
        direction       : str   "BUY" (target_change>0) / "SELL" (<0)
    """
    target_changes = {
        m: v for m, v in net.items()
        if m != SOL_KEY and m not in STABLE_LABELS and v != 0
    }
    if not target_changes:
        return None
    target_mint, target_change = max(target_changes.items(), key=lambda kv: abs(kv[1]))

    quote_label: str | None = None
    quote_mint: str | None = None
    quote_change = 0.0

    # stable 優先 (USDC > USDT > USD1 ではなく abs 最大)
    stables = {m: v for m, v in net.items() if m in STABLE_LABELS and v != 0}
    if stables:
        qm, qv = max(stables.items(), key=lambda kv: abs(kv[1]))
        if target_change * qv < 0:
            quote_label = STABLE_LABELS[qm]
            quote_mint = qm
            quote_change = qv

    # stable で確定しなければ SOL を試す
    if quote_label is None:
        sol_v = net.get(SOL_KEY, 0.0)
        if sol_v != 0 and target_change * sol_v < 0:
            quote_label = "SOL"
            quote_mint = WSOL_MINT
            quote_change = sol_v

    if quote_label is None or quote_mint is None:
        return None

    return {
        "target_mint": target_mint,
        "target_change": target_change,
        "quote_label": quote_label,
        "quote_mint": quote_mint,
        "quote_change": quote_change,
        "direction": "BUY" if target_change > 0 else "SELL",
    }
