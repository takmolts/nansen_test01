"""Discord Embed 生成モジュール。

Nansen API のレスポンス形式は公式ドキュメントを参照すれば確定するが、
このミニマム実装ではフィールド名を複数候補から順に探す方針で実装し、
多少スキーマが変わっても落ちにくいようにしている。
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    from bot.scoring.types import TotalScore

COLOR_PRIMARY = 0x4B9CD3     # 青系(情報)
COLOR_WARN = 0xFFD700        # 黄(集中度/バンドル軽度)
COLOR_DANGER = 0xFF4444      # 赤(バンドル検出あり)

# 総合スコアのバンド色
COLOR_BAND = {
    "STRONG BUY": 0x00FF00,
    "BUY": 0x7FFF00,
    "CAUTION": 0xFFD700,
    "AVOID": 0xFF4444,
}


# =========================
# 共通ヘルパー
# =========================

def _first(d: Any, *keys: str, default: Any = None) -> Any:
    """dict から候補キーの中で最初にヒットしたものを返す。"""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _unwrap(data: Any, *keys: str) -> Any:
    """ネストされた dict を辿る。途中で dict でなくなれば None。"""
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # よくある {"data": [...]} パターン
        for key in ("data", "items", "result", "results", "holders", "wallets"):
            v = value.get(key)
            if isinstance(v, list):
                return v
    return []


def _short_addr(address: str, head: int = 4, tail: int = 4) -> str:
    if not isinstance(address, str) or len(address) <= head + tail + 3:
        return str(address)
    return f"{address[:head]}...{address[-tail:]}"


def _fmt_usd(value: Any) -> str:
    n = _to_float(value)
    if n is None:
        return "N/A"
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"${n/1_000:.2f}K"
    if abs_n >= 1:
        return f"${n:.2f}"
    return f"${n:.6f}"


def _fmt_pct(value: Any, *, signed: bool = False) -> str:
    n = _to_float(value)
    if n is None:
        return "N/A"
    if signed:
        return f"{n:+.2f}%"
    return f"{n:.2f}%"


def _fmt_int(value: Any) -> str:
    n = _to_float(value)
    if n is None:
        return "N/A"
    return f"{int(n):,}"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _solscan_url(address: str) -> str:
    return f"https://solscan.io/token/{address}"


def _solscan_account_url(address: str) -> str:
    return f"https://solscan.io/account/{address}"


# =========================
# 銘柄詳細 Embed
# =========================

def build_token_info_embed(
    token_info: Any,
    token_address: str,
    *,
    error: str | None = None,
) -> discord.Embed:
    """Nansen `/tgm/token-information` のレスポンスを Embed 化する。

    スキーマ (docs.nansen.ai):
        data.name / data.symbol / data.logo / data.contract_address
        data.token_details.{market_cap_usd, fdv_usd, circulating_supply,
                            total_supply, token_deployment_date,
                            website, x, telegram}
        data.spot_metrics.{volume_total_usd, buy_volume_usd, sell_volume_usd,
                           total_buys, total_sells, unique_buyers, unique_sellers,
                           liquidity_usd, total_holders}
    timeframe は request で 1d を指定しているので表示は 24h 扱い。
    """
    if error is not None:
        embed = discord.Embed(
            title="📄 Token Info",
            description=f"取得に失敗しました: `{error}`",
            color=COLOR_DANGER,
        )
        embed.add_field(
            name="CA",
            value=f"[{_short_addr(token_address)}]({_solscan_url(token_address)})",
            inline=False,
        )
        return embed

    data = _unwrap(token_info, "data")
    if data is None:
        data = token_info
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        data = {}

    token_details = data.get("token_details") if isinstance(data.get("token_details"), dict) else {}
    spot = data.get("spot_metrics") if isinstance(data.get("spot_metrics"), dict) else {}

    name = _first(data, "name", "token_name", default="(unknown)")
    symbol = _first(data, "symbol", "token_symbol", default="?")
    logo = _first(data, "logo", "logo_url")

    liquidity = _first(spot, "liquidity_usd")
    volume = _first(spot, "volume_total_usd", "volume_usd")
    buy_vol = _first(spot, "buy_volume_usd")
    sell_vol = _first(spot, "sell_volume_usd")
    holders_total = _first(spot, "total_holders")

    market_cap = _first(token_details, "market_cap_usd", "market_cap")
    fdv = _first(token_details, "fdv_usd", "fdv")
    deployment = _first(token_details, "token_deployment_date", "deployment_date")
    website = _first(token_details, "website")
    twitter_url = _first(token_details, "x", "twitter")
    telegram_url = _first(token_details, "telegram")

    age_str = _format_age(deployment)
    ratio_str = _format_buy_sell_ratio(buy_vol, sell_vol)

    embed = discord.Embed(
        title=f"📄 ${symbol} — {name}",
        url=_solscan_url(token_address),
        description=f"Chain: `solana` | Age: {age_str}\nCA: `{token_address}`",
        color=COLOR_PRIMARY,
    )
    if isinstance(logo, str) and logo.startswith("http"):
        embed.set_thumbnail(url=logo)

    embed.add_field(name="💧 Liquidity", value=_fmt_usd(liquidity), inline=True)
    embed.add_field(name="🏛 MCap", value=_fmt_usd(market_cap), inline=True)
    embed.add_field(name="💎 FDV", value=_fmt_usd(fdv), inline=True)
    embed.add_field(name="📊 Volume 24h", value=_fmt_usd(volume), inline=True)
    embed.add_field(name="🔁 Buy/Sell (24h)", value=ratio_str, inline=True)
    embed.add_field(name="👥 Holders", value=_fmt_int(holders_total), inline=True)

    socials: list[str] = []
    if isinstance(website, str) and website.startswith("http"):
        socials.append(f"[Web]({website})")
    if isinstance(twitter_url, str) and twitter_url.startswith("http"):
        socials.append(f"[X]({twitter_url})")
    if isinstance(telegram_url, str) and telegram_url.startswith("http"):
        socials.append(f"[TG]({telegram_url})")
    if socials:
        embed.add_field(name="🔗 Socials", value=" | ".join(socials), inline=False)

    return embed


def _format_age(deployment: Any) -> str:
    if not isinstance(deployment, str) or not deployment:
        return "N/A"
    try:
        deploy_dt = datetime.fromisoformat(deployment.replace("Z", "+00:00"))
    except ValueError:
        return deployment[:10]
    if deploy_dt.tzinfo is None:
        deploy_dt = deploy_dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - deploy_dt
    days = delta.days
    if days >= 1:
        return f"{days}d"
    hours = delta.seconds // 3600
    return f"{hours}h"


def _format_buy_sell_ratio(buy_vol: Any, sell_vol: Any) -> str:
    bv = _to_float(buy_vol)
    sv = _to_float(sell_vol)
    if bv is None or sv is None:
        return "N/A"
    if sv <= 0:
        return "∞" if bv > 0 else "N/A"
    return f"{bv / sv:.2f}x"


# =========================
# Smart Wallets Embed
# =========================

def build_smart_wallets_embed(
    who_bought_sold: Any,
    *,
    limit: int = 10,
    error: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 Smart Wallets (直近のSM売買)",
        color=COLOR_PRIMARY,
    )
    if error is not None:
        embed.description = f"取得に失敗しました: `{error}`"
        embed.color = COLOR_DANGER
        return embed

    wallets = _as_list(who_bought_sold)
    if not wallets:
        # 入れ子の可能性
        for key in ("buyers", "sellers", "smartMoney", "smart_money"):
            nested = _unwrap(who_bought_sold, key)
            if isinstance(nested, list) and nested:
                wallets = nested
                break

    if not wallets:
        embed.description = "Smart Money の売買履歴は見つかりませんでした。"
        return embed

    lines: list[str] = []
    for w in wallets[:limit]:
        if not isinstance(w, dict):
            continue
        addr = _first(w, "address", "wallet_address", "walletAddress", default="")
        label = _first(
            w,
            "label", "smart_money_label", "entity_label", "walletLabel", "name",
            default="",
        )
        side = _first(w, "buy_or_sell", "side", "action", default="")
        usd = _first(
            w,
            "bought_volume_usd", "sold_volume_usd",
            "trade_volume_usd", "volume_usd", "usd_value",
            "volumeUsd", "usdValue", "amountUsd",
        )
        short = _short_addr(addr) if addr else "(no addr)"
        link = f"[{short}]({_solscan_account_url(addr)})" if addr else short
        parts = [link]
        if label:
            parts.append(f"`{label}`")
        if side:
            parts.append(f"{side}")
        if usd is not None:
            parts.append(_fmt_usd(usd))
        lines.append(" — ".join(parts))

    if not lines:
        embed.description = "表示可能な Smart Wallet エントリがありません。"
        return embed

    embed.description = "\n".join(lines)
    if len(wallets) > limit:
        embed.set_footer(text=f"(上位 {limit}/{len(wallets)} 件のみ表示)")
    return embed


# =========================
# ホルダー分布 Embed
# =========================

def extract_holders(holders_data: Any) -> list[dict[str, Any]]:
    """holders レスポンスから holder dict のリストを取り出す。"""
    candidates = _as_list(holders_data)
    if candidates:
        return [h for h in candidates if isinstance(h, dict)]
    for key in ("holders", "top_holders", "topHolders"):
        nested = _unwrap(holders_data, key)
        if isinstance(nested, list):
            return [h for h in nested if isinstance(h, dict)]
    return []


def holder_pct(h: dict[str, Any]) -> float | None:
    """ホルダー dict から保有割合(%)を取り出す。"""
    v = _first(
        h,
        "ownership_percentage", "percentage", "pct",
        "share_pct", "sharePct", "ownership", "balancePct",
    )
    n = _to_float(v)
    if n is None:
        return None
    # 0〜1 の比率で返ってくる場合は 100倍
    if 0 < n <= 1:
        n *= 100
    return n


def holder_address(h: dict[str, Any]) -> str:
    return str(_first(h, "address", "wallet_address", "walletAddress", default=""))


def build_holders_embed(
    holders_data: Any,
    *,
    top_n: int = 10,
    error: str | None = None,
    total_holders_override: int | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="👥 Holders 分布",
        color=COLOR_PRIMARY,
    )
    if error is not None:
        embed.description = f"取得に失敗しました: `{error}`"
        embed.color = COLOR_DANGER
        return embed

    holders = extract_holders(holders_data)
    if not holders:
        embed.description = "ホルダー情報が取得できませんでした。"
        return embed

    sorted_holders = sorted(
        holders,
        key=lambda h: holder_pct(h) or 0.0,
        reverse=True,
    )
    top = sorted_holders[:top_n]

    top_sum = sum((holder_pct(h) or 0.0) for h in top)
    total_holders: Any = total_holders_override
    if total_holders is None and isinstance(holders_data, dict):
        total_holders = _first(holders_data, "total_holders", "totalHolders", "total")

    concentration_color = (
        COLOR_DANGER if top_sum >= 50
        else COLOR_WARN if top_sum >= 30
        else COLOR_PRIMARY
    )
    embed.color = concentration_color

    header_lines = [f"トップ{len(top)}集中度: **{top_sum:.2f}%**"]
    if total_holders is not None:
        header_lines.append(f"総ホルダー数: {_fmt_int(total_holders)}")
    embed.description = "\n".join(header_lines)

    lines: list[str] = []
    for i, h in enumerate(top, start=1):
        addr = holder_address(h)
        pct = holder_pct(h)
        label = _first(h, "label", "walletLabel", "name", default="")
        short = _short_addr(addr) if addr else "(no addr)"
        link = f"[{short}]({_solscan_account_url(addr)})" if addr else short
        tail = f" `{label}`" if label else ""
        lines.append(f"{i:>2}. {link} — {_fmt_pct(pct)}{tail}")

    embed.add_field(name=f"Top {len(top)} Holders", value="\n".join(lines) or "-", inline=False)
    return embed


# =========================
# バンドル検出 Embed
# =========================

def find_first_funders(related_data: Any) -> list[str]:
    """related-wallets レスポンスから First Funder のアドレスを抽出する。"""
    items = _as_list(related_data)
    if not items:
        for key in (
            "related_wallets", "relatedWallets",
            "connections", "special_connections", "specialConnections",
        ):
            nested = _unwrap(related_data, key)
            if isinstance(nested, list):
                items = nested
                break

    funders: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rel_type = str(
            _first(
                item,
                "type", "relation", "relation_type", "relationType",
                "connection_type", "connectionType",
                default="",
            )
        ).lower()
        if "first funder" in rel_type or rel_type == "funder":
            addr = _first(item, "address", "wallet_address", "walletAddress")
            if isinstance(addr, str) and addr:
                funders.append(addr)
    return funders


def build_bundle_embed(
    clusters: list[tuple[str, list[dict[str, Any]]]],
    whales: list[dict[str, Any]],
    *,
    error: str | None = None,
) -> discord.Embed:
    """
    clusters: [(funder_address, [holder_dict, ...]), ...]  (2件以上のホルダーを共有する funder のみ)
    whales:   全 whale (3%超) のリスト
    """
    embed = discord.Embed(
        title="📦 Bundle Detection",
        color=COLOR_PRIMARY,
    )
    if error is not None:
        embed.description = f"取得に失敗しました: `{error}`"
        embed.color = COLOR_DANGER
        return embed

    if not whales:
        embed.description = "3%超のホルダーがいないためバンドル判定をスキップしました。"
        return embed

    if not clusters:
        embed.description = (
            f"3%超ホルダー {len(whales)} 件を確認しましたが、"
            "共通の First Funder を持つクラスタは検出されませんでした。"
        )
        return embed

    # クラスタが検出された = 警告
    max_cluster_size = max(len(h) for _, h in clusters)
    if max_cluster_size >= 3:
        embed.color = COLOR_DANGER
    else:
        embed.color = COLOR_WARN

    lines: list[str] = []
    lines.append(f"3%超ホルダー {len(whales)} 件 / 検出クラスタ {len(clusters)} 件")
    lines.append("")
    for i, (funder, members) in enumerate(clusters, start=1):
        total_pct = sum((holder_pct(m) or 0.0) for m in members)
        funder_link = f"[{_short_addr(funder)}]({_solscan_account_url(funder)})"
        lines.append(f"**Cluster {chr(64+i)}** ({len(members)}wallets, 合計 {total_pct:.2f}%)")
        lines.append(f"└─ First Funder: {funder_link}")
        for m in members:
            addr = holder_address(m)
            pct = holder_pct(m)
            short = _short_addr(addr) if addr else "(no addr)"
            link = f"[{short}]({_solscan_account_url(addr)})" if addr else short
            lines.append(f"  ├─ {link} — {_fmt_pct(pct)}")
        lines.append("")

    embed.description = "\n".join(lines).strip()
    return embed


# =========================
# フッタ付与
# =========================

def build_summary_embed(scores: "TotalScore", symbol: str) -> discord.Embed:
    """総合スコア + カテゴリ別スコアの一覧 (フェーズA時点では5カテゴリ)。"""
    title = f"🎯 ${symbol} Analysis Summary" if symbol else "🎯 Analysis Summary"
    color = COLOR_BAND.get(scores.band, COLOR_PRIMARY)

    desc_lines = [
        f"**Total: {scores.total:.1f} / 100  {scores.band_emoji} {scores.band}**",
        "",
        "```",
    ]
    name_width = max((len(c.name) for c in scores.categories), default=0)
    for c in scores.categories:
        bar = _make_bar(c.score)
        desc_lines.append(
            f"{c.emoji} {c.name:<{name_width}}  {c.score:5.1f}  {bar}"
        )
    desc_lines.append("```")
    desc_lines.append("\n*フェーズB2: 7カテゴリで暫定算出 (Narrative は未実装)*")

    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=color,
    )
    return embed


def _make_bar(score: float, width: int = 10) -> str:
    clamped = max(0.0, min(100.0, score))
    filled = int(clamped / (100 / width))
    return "█" * filled + "░" * (width - filled)


def set_credit_footer(embeds: Iterable[discord.Embed], credits: int) -> None:
    """最後の Embed に「消費クレジット(目安)」をフッタとして付ける。"""
    last: discord.Embed | None = None
    for e in embeds:
        last = e
    if last is None:
        return
    last.set_footer(text=f"消費クレジット: {credits}(目安)")
