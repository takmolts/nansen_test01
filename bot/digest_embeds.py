"""/digest コマンド用の簡略 Embed 生成。

DIGEST_CHANNEL_ID へは 3 カテゴリ (出来高 / SM / 急流入) の TOP5 を
1 つの Embed に 3 fields でまとめた compact 版を投稿する。
詳細な per-token Embed は archive thread 側 (bot/archive_embeds.py) で生成する。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import discord

COLOR_MOMENTUM = 0xFF8C00

ROWS_PER_EMBED = 5  # カテゴリ毎の表示件数


def build_digest_summary_embed(
    *,
    momentum_resp: Any,
    sm_resp: Any,
    danger_resp: Any,
    credits_used: int,
    timeframe: str = "24h",
    archive_jump_url: str | None = None,
) -> discord.Embed:
    """3 カテゴリの TOP5 を 1 つの Embed (3 fields) にまとめた簡略版を返す。

    ・ 各行は 1 line で symbol が DexScreener ハイパーリンク
    ・ 詳細は archive thread の **その集計のヘッダーメッセージ** にジャンプリンク
      (`archive_jump_url`)。 archive thread が無い場合は description 空。
    """
    embed = discord.Embed(
        title=f"📊 Digest ({timeframe})",
        color=COLOR_MOMENTUM,
    )
    if archive_jump_url:
        embed.description = f"[📂 この集計の詳細を見る]({archive_jump_url})"

    embed.add_field(
        name=f"🔥 出来高急増ミーム ({timeframe}, age ≤ 30d)",
        value=_summary_lines(momentum_resp, _format_summary_momentum),
        inline=False,
    )
    embed.add_field(
        name=f"🧠 Smart Money 買い集め ({timeframe})",
        value=_summary_lines(sm_resp, _format_summary_sm),
        inline=False,
    )
    embed.add_field(
        name=f"⚡ 急流入 ({timeframe}, age ≤ 7d)",
        value=_summary_lines(danger_resp, _format_summary_danger),
        inline=False,
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    embed.set_footer(
        text=f"消費クレジット: {credits_used}(目安) | 集計: {now} | timeframe: {timeframe}"
    )
    return embed


def _summary_lines(
    resp: Any,
    formatter: Callable[[int, dict], str],
) -> str:
    """カテゴリ 1 つぶんの compact 行文字列を返す。 取得失敗 / 空 にも対応。"""
    err = _err_msg(resp)
    if err is not None:
        return f"取得失敗: `{err}`"
    data = _extract_data(resp)
    if not data:
        return "*該当トークンなし*"
    lines = [formatter(i + 1, t) for i, t in enumerate(data[:ROWS_PER_EMBED])]
    return "\n".join(lines)


def _summary_symbol_link(t: dict[str, Any]) -> str:
    sym = (t.get("token_symbol") or "?").upper()
    addr = t.get("token_address") or ""
    if addr:
        return f"[**${sym}**](https://dexscreener.com/solana/{addr})"
    return f"**${sym}**"


def _format_summary_momentum(rank: int, t: dict[str, Any]) -> str:
    vol = _fmt_usd(t.get("volume"))
    pc = t.get("price_change")
    pc_str = _fmt_pct_signed(pc) if pc is not None else "?"
    mcap = _fmt_usd(t.get("market_cap_usd"))
    return f"`{rank}.` {_summary_symbol_link(t)} — vol {vol} ({pc_str}) · mcap {mcap}"


def _format_summary_sm(rank: int, t: dict[str, Any]) -> str:
    bv = _fmt_usd(t.get("buy_volume"))
    nb = _trader_count(t)
    mcap = _fmt_usd(t.get("market_cap_usd"))
    nb_str = f"{nb} traders · " if nb is not None else ""
    return f"`{rank}.` {_summary_symbol_link(t)} — SM buy {bv} · {nb_str}mcap {mcap}"


def _format_summary_danger(rank: int, t: dict[str, Any]) -> str:
    ratio = t.get("inflow_fdv_ratio")
    ratio_str = f"{ratio:.2f}x" if isinstance(ratio, (int, float)) else "?"
    fdv = _fmt_usd(t.get("fdv"))
    nb = _trader_count(t)
    nb_str = f" · {nb} traders" if nb is not None else ""
    age = _fmt_age_value(t.get("token_age_days"))
    return f"`{rank}.` {_summary_symbol_link(t)} — inflow/FDV {ratio_str} · fdv {fdv} · age {age}{nb_str}"


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
