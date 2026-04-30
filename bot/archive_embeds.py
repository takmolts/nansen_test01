"""digest アーカイブスレッド用の Embed 生成。

1 ミーム 1 Embed で、 DexScreener のバナー (image) とアイコン (thumbnail) 付き。
既存の token-screener レスポンスを使い回し、 DexScreener から price / liquidity /
txns 等の補足を上乗せして 1 つの Embed にまとめる。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from bot.links import grok_token_link_md, trade_links_md, x_search_links_md

COLOR_MOMENTUM = 0xFF8C00
COLOR_SM = 0x4B9CD3
COLOR_HOT = 0x9370DB

CATEGORIES = ("momentum", "sm", "hot")
CATEGORY_LABEL = {
    "momentum": "🔥 出来高急増",
    "sm": "🧠 Smart Money",
    "hot": "⚡ 急流入",
}
CATEGORY_COLOR = {
    "momentum": COLOR_MOMENTUM,
    "sm": COLOR_SM,
    "hot": COLOR_HOT,
}


def build_archive_embed(
    *,
    category: str,
    rank: int,
    timeframe: str,
    screener_data: dict[str, Any],
    dex_data: dict[str, Any] | None,
    timestamp: datetime | None = None,
) -> discord.Embed:
    sym = (screener_data.get("token_symbol") or "?").upper()
    addr = screener_data.get("token_address") or ""
    chain = (screener_data.get("chain") or "solana").lower()

    # screener data
    vol = _fmt_usd(screener_data.get("volume"))
    pc = screener_data.get("price_change")
    pc_str = _fmt_pct_signed(pc) if pc is not None else "?"
    mcap = _fmt_usd(screener_data.get("market_cap_usd"))
    fdv = _fmt_usd(screener_data.get("fdv"))
    age = _fmt_age_value(screener_data.get("token_age_days"))
    inflow_ratio = screener_data.get("inflow_fdv_ratio")
    buy_volume_screener = _fmt_usd(screener_data.get("buy_volume"))
    nb = _trader_count(screener_data)

    # DexScreener 詳細
    icon = _dex_icon(dex_data)
    price_usd = _fmt_price(_dex_get(dex_data, "priceUsd"))
    liquidity = _dex_get(dex_data, "liquidity", "usd")
    liq_str = _fmt_usd(liquidity)
    txns_h24 = _dex_get(dex_data, "txns", "h24") or {}
    buys = txns_h24.get("buys") if isinstance(txns_h24, dict) else None
    sells = txns_h24.get("sells") if isinstance(txns_h24, dict) else None
    pair_url = _dex_get(dex_data, "url")

    cat_label = CATEGORY_LABEL.get(category, category)
    color = CATEGORY_COLOR.get(category, 0x4B9CD3)

    embed = discord.Embed(
        title=f"{cat_label} #{rank}  ${sym}",
        url=pair_url if isinstance(pair_url, str) else None,
        color=color,
        timestamp=timestamp or datetime.now(timezone.utc),
    )

    lines: list[str] = []
    if price_usd != "N/A":
        lines.append(f"💵 price: {price_usd}")
    lines.append(f"🪙 mcap: {mcap}")
    if fdv != "N/A":
        lines.append(f"💎 fdv: {fdv}")
    lines.append(f"⚡ vol: {vol} ({pc_str})")
    if liq_str != "N/A":
        lines.append(f"💧 liquidity: {liq_str}")
    if buys is not None and sells is not None:
        try:
            tot = int(buys) + int(sells)
            lines.append(f"📊 txns 24h: {tot} (buy {int(buys)} / sell {int(sells)})")
        except (TypeError, ValueError):
            pass
    lines.append(f"🕒 age: {age}")

    # カテゴリ別の補足
    if category == "sm":
        if buy_volume_screener != "N/A":
            lines.append(f"💵 SM buy ({timeframe}): {buy_volume_screener}")
        if nb is not None:
            lines.append(f"👥 traders ({timeframe}): {nb}")
    elif category == "hot" and isinstance(inflow_ratio, (int, float)):
        lines.append(f"💧 inflow/FDV ({timeframe}): {inflow_ratio:.2f}x")

    lines.append(f"💬 CA: `{addr}`")
    sym_for_link = sym if sym and sym != "?" else None
    x_md = x_search_links_md(sym_for_link, addr)
    if x_md:
        lines.append(f"🐦 X Search: {x_md}")
    grok_md = grok_token_link_md(sym_for_link, addr)
    if grok_md:
        lines.append(f"🤖 Grok: {grok_md}")
    lines.append(f"🔗 Trade: {trade_links_md(addr, chain=chain)}")

    embed.description = "\n".join(lines)

    if icon:
        embed.set_thumbnail(url=icon)
    if addr:
        # DexScreener の og バナー (チェーン別)
        embed.set_image(url=f"https://cdn.dexscreener.com/token-images/og/{chain}/{addr}")

    embed.set_footer(text=f"timeframe: {timeframe}")
    return embed


# ----- ヘルパ -----

def _dex_get(data: Any, *keys: str) -> Any:
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _dex_icon(dex: Any) -> str | None:
    info = _dex_get(dex, "info")
    if not isinstance(info, dict):
        return None
    img = info.get("imageUrl")
    return img if isinstance(img, str) and img.startswith("http") else None


def _trader_count(t: dict[str, Any]) -> int | None:
    for key in ("nof_buyers", "nof_traders", "nof_buys"):
        v = t.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


def _fmt_usd(v: Any) -> str:
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    a = abs(n)
    if a >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if a >= 1_000:
        return f"${n/1_000:.2f}K"
    if a >= 1:
        return f"${n:.2f}"
    return f"${n:.6f}"


def _fmt_price(v: Any) -> str:
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if abs(n) >= 1:
        return f"${n:.4f}"
    return f"${n:.8f}"


def _fmt_pct_signed(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    return f"{n:+.2f}%"


def _fmt_age_value(v: Any) -> str:
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    if n < 1:
        return f"{n*24:.0f}h"
    if n < 30:
        return f"{n:.1f}d"
    if n < 365:
        return f"{n/30:.1f}mo"
    return f"{n/365:.1f}y"
