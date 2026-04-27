"""/digest コマンド用の Embed 生成。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from bot.links import trade_links_md, x_search_links_md

COLOR_MOMENTUM = 0xFF8C00      # オレンジ (勢い)
COLOR_SM = 0x4B9CD3            # 青 (SM)
COLOR_HOT = 0x9370DB           # 紫 (急流入、 両刃シグナル)
COLOR_ERROR = 0x808080

# 各 Embed あたりの表示件数 (1 メッセージ 1 Embed で送るので 1 embed 6000 制限のみ)
ROWS_PER_EMBED = 5


def build_digest_embeds(
    *,
    momentum_resp: Any,
    sm_resp: Any,
    danger_resp: Any,
    credits_used: int,
    timeframe: str = "24h",
) -> list[discord.Embed]:
    embeds = [
        _build_embed(
            title=f"🔥 出来高急増ミーム ({timeframe}, age ≤ 30d)",
            description=f"出来高 (`volume`) 上位 {ROWS_PER_EMBED} 件。 直近 {timeframe} で資金が集まっているトークン。",
            color=COLOR_MOMENTUM,
            data=_extract_data(momentum_resp),
            row_formatter=_format_row_momentum,
            error=_err_msg(momentum_resp),
        ),
        _build_embed(
            title=f"🧠 Smart Money 買い集めランキング ({timeframe})",
            description=f"SM の買い額 (`buy_volume`) 上位 {ROWS_PER_EMBED} 件。 直近 {timeframe} でプロが買っているトークン。",
            color=COLOR_SM,
            data=_extract_data(sm_resp),
            row_formatter=_format_row_sm,
            error=_err_msg(sm_resp),
        ),
        _build_embed(
            title=f"⚡ 急流入トークン ({timeframe}, age ≤ 7d, FDV比 流入大)",
            description=(
                "新規 7 日以内 × `inflow_fdv_ratio` 上位。 \n"
                "SM / インサイダー早期流入の可能性、 一方で短期ポンプの典型でもある両刃シグナル。 "
                "入るならエントリー早く、 出口を意識して。"
            ),
            color=COLOR_HOT,
            data=_extract_data(danger_resp),
            row_formatter=_format_row_danger,
            error=_err_msg(danger_resp),
        ),
    ]

    last = embeds[-1]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last.set_footer(text=f"消費クレジット: {credits_used}(目安) | 集計: {now} | timeframe: {timeframe}")
    return embeds


def _build_embed(
    *,
    title: str,
    description: str,
    color: int,
    data: list[dict[str, Any]],
    row_formatter,
    error: str | None,
) -> discord.Embed:
    if error is not None:
        embed = discord.Embed(
            title=title,
            description=f"取得に失敗: `{error}`",
            color=COLOR_ERROR,
        )
        return embed

    if not data:
        embed = discord.Embed(
            title=title,
            description=f"{description}\n\n*該当トークンなし*",
            color=color,
        )
        return embed

    lines = [description, ""]
    for i, t in enumerate(data[:ROWS_PER_EMBED], start=1):
        lines.append(row_formatter(i, t))
        lines.append("")  # 行間スペース

    embed = discord.Embed(
        title=title,
        description="\n".join(lines).rstrip(),
        color=color,
    )
    return embed


# ----- 行フォーマッタ -----

def _format_row_momentum(rank: int, t: dict[str, Any]) -> str:
    sym = (t.get("token_symbol") or "?").upper()
    addr = t.get("token_address") or ""
    vol = _fmt_usd(t.get("volume"))
    pc = t.get("price_change")
    pc_str = _fmt_pct_signed(pc) if pc is not None else "?"
    mcap = _fmt_usd(t.get("market_cap_usd"))
    age = _fmt_age_value(t.get("token_age_days"))

    metrics = [
        f"🪙 mcap: {mcap}",
        f"⚡ vol: {vol} ({pc_str})",
        f"🕒 age: {age}",
    ]
    return _compose_row(rank, sym, addr, metrics)


def _format_row_sm(rank: int, t: dict[str, Any]) -> str:
    sym = (t.get("token_symbol") or "?").upper()
    addr = t.get("token_address") or ""
    bv = _fmt_usd(t.get("buy_volume"))
    mcap = _fmt_usd(t.get("market_cap_usd"))
    nb = _trader_count(t)

    metrics = [f"💵 SM buy: {bv}"]
    if nb is not None:
        metrics.append(f"👥 traders: {nb}")
    metrics.append(f"🪙 mcap: {mcap}")
    return _compose_row(rank, sym, addr, metrics)


def _format_row_danger(rank: int, t: dict[str, Any]) -> str:
    sym = (t.get("token_symbol") or "?").upper()
    addr = t.get("token_address") or ""
    age = _fmt_age_value(t.get("token_age_days"))
    ratio = t.get("inflow_fdv_ratio")
    ratio_str = f"{ratio:.2f}x" if isinstance(ratio, (int, float)) else "?"
    fdv = _fmt_usd(t.get("fdv"))
    nb = _trader_count(t)

    metrics = [
        f"🕒 age: {age}",
        f"💧 inflow/FDV: {ratio_str}",
        f"💎 fdv: {fdv}",
    ]
    if nb is not None:
        metrics.append(f"👥 traders: {nb}")
    return _compose_row(rank, sym, addr, metrics)


def _compose_row(rank: int, sym: str, addr: str, metrics: list[str]) -> str:
    """ヘッダ (rank + symbol) を 1 行、 metrics を縦並び、 リンクを 3 行で返す。

    太字 (`**`) は使わない。 絵文字混じり symbol だと markdown レンダリングが
    崩れるため。
    """
    header = f"{rank}. ${sym}"
    parts = [header] + metrics + [_link_line(addr, sym)]
    return "\n".join(parts)


def _trader_count(t: dict[str, Any]) -> int | None:
    """nof_buyers / nof_traders / nof_buys のうち取れた値を返す。"""
    for key in ("nof_buyers", "nof_traders", "nof_buys"):
        v = t.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


# ----- 共通ヘルパ -----

def _link_line(addr: str, symbol: str | None = None) -> str:
    """CA / X Search / Trade を 3 行に分けて返す。"""
    if not addr:
        return "  (no address)"
    sym = symbol if symbol and symbol != "?" else None
    trade = trade_links_md(addr, chain="solana")
    x = x_search_links_md(sym, addr)
    lines = [f"💬 CA: `{addr}`"]
    if x:
        lines.append(f"🐦 X Search: {x}")
    lines.append(f"🔗 Trade: {trade}")
    return "\n".join(lines)


def _extract_data(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, BaseException) or not isinstance(resp, dict):
        return []
    data = resp.get("data")
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _err_msg(resp: Any) -> str | None:
    if not isinstance(resp, BaseException):
        return None
    return type(resp).__name__


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
