"""/digest コマンド用の Embed 生成。

各カテゴリ (出来高 / SM / 急流入) ごとに 1 メッセージ分の Embed リストを返す:
  [カテゴリ見出し Embed, トークン1 Embed, ..., トークン5 Embed]

各トークン Embed は thumbnail にアイコン画像を持ち、 title が ranking、
description にメトリクスと CA / X Search / Trade リンクが入る。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import discord

from bot.links import trade_links_md, x_search_links_md

COLOR_MOMENTUM = 0xFF8C00
COLOR_SM = 0x4B9CD3
COLOR_HOT = 0x9370DB
COLOR_ERROR = 0x808080

ROWS_PER_EMBED = 5  # カテゴリ毎の表示件数


def build_digest_message_groups(
    *,
    momentum_resp: Any,
    sm_resp: Any,
    danger_resp: Any,
    image_urls: dict[str, str | None],
    credits_used: int,
    timeframe: str = "24h",
) -> list[list[discord.Embed]]:
    """カテゴリ毎の Embed リスト 3 つを返す。 各リストは 1 メッセージとして送る想定。"""
    groups = [
        _build_category(
            title=f"🔥 出来高急増ミーム ({timeframe}, age ≤ 30d)",
            description=f"出来高 (`volume`) 上位 {ROWS_PER_EMBED} 件。 直近 {timeframe} で資金が集まっているトークン。",
            color=COLOR_MOMENTUM,
            data=_extract_data(momentum_resp),
            row_formatter=_format_row_momentum,
            image_urls=image_urls,
            error=_err_msg(momentum_resp),
        ),
        _build_category(
            title=f"🧠 Smart Money 買い集めランキング ({timeframe})",
            description=f"SM の買い額 (`buy_volume`) 上位 {ROWS_PER_EMBED} 件。 直近 {timeframe} でプロが買っているトークン。",
            color=COLOR_SM,
            data=_extract_data(sm_resp),
            row_formatter=_format_row_sm,
            image_urls=image_urls,
            error=_err_msg(sm_resp),
        ),
        _build_category(
            title=f"⚡ 急流入トークン ({timeframe}, age ≤ 7d, FDV比 流入大)",
            description=(
                "新規 7 日以内 × `inflow_fdv_ratio` 上位。 \n"
                "SM / インサイダー早期流入の可能性、 一方で短期ポンプの典型でもある両刃シグナル。 "
                "入るならエントリー早く、 出口を意識して。"
            ),
            color=COLOR_HOT,
            data=_extract_data(danger_resp),
            row_formatter=_format_row_danger,
            image_urls=image_urls,
            error=_err_msg(danger_resp),
        ),
    ]

    # 最後のグループの最後の embed のフッタに集計時刻と消費クレジット
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer_text = f"消費クレジット: {credits_used}(目安) | 集計: {now} | timeframe: {timeframe}"
    if groups[-1]:
        groups[-1][-1].set_footer(text=footer_text)

    return groups


def _build_category(
    *,
    title: str,
    description: str,
    color: int,
    data: list[dict[str, Any]],
    row_formatter: Callable[[int, dict], tuple[str, str]],
    image_urls: dict[str, str | None],
    error: str | None,
) -> list[discord.Embed]:
    """カテゴリ見出し + 各トークンの Embed リストを返す。"""
    if error is not None:
        head = discord.Embed(
            title=title,
            description=f"取得に失敗: `{error}`",
            color=COLOR_ERROR,
        )
        return [head]

    head = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    if not data:
        head.description = f"{description}\n\n*該当トークンなし*"
        return [head]

    out = [head]
    for i, t in enumerate(data[:ROWS_PER_EMBED], start=1):
        embed_title, body = row_formatter(i, t)
        addr = t.get("token_address") or ""
        embed = discord.Embed(
            title=embed_title,
            description=body,
            color=color,
        )
        img = image_urls.get(addr.lower()) if addr else None
        if img:
            embed.set_thumbnail(url=img)
        out.append(embed)
    return out


# ----- 行フォーマッタ: (title, body) を返す -----

def _format_row_momentum(rank: int, t: dict[str, Any]) -> tuple[str, str]:
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


def _format_row_sm(rank: int, t: dict[str, Any]) -> tuple[str, str]:
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


def _format_row_danger(rank: int, t: dict[str, Any]) -> tuple[str, str]:
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


def _compose_row(rank: int, sym: str, addr: str, metrics: list[str]) -> tuple[str, str]:
    title = f"{rank}. ${sym}"
    body = "\n".join(metrics + [_link_block(addr, sym)])
    return title, body


def _link_block(addr: str, symbol: str | None = None) -> str:
    if not addr:
        return "(no address)"
    sym = symbol if symbol and symbol != "?" else None
    trade = trade_links_md(addr, chain="solana")
    x = x_search_links_md(sym, addr)
    lines = [f"💬 CA: `{addr}`"]
    if x:
        lines.append(f"🐦 X Search: {x}")
    lines.append(f"🔗 Trade: {trade}")
    return "\n".join(lines)


def _trader_count(t: dict[str, Any]) -> int | None:
    for key in ("nof_buyers", "nof_traders", "nof_buys"):
        v = t.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


# ----- 共通ヘルパ -----

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
