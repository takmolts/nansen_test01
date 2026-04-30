"""トークン CA から取引/閲覧用 URL を生成する共通ヘルパ。"""
from __future__ import annotations

import urllib.parse

# UniversalX の chainId 対応 (CAIP 風数値)
_UNIVERSALX_CHAIN_ID = {
    "solana": "101",
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "avalanche": "43114",
    "mantle": "5000",
    "monad": "143",
}

# gmgn の chain slug 対応
_GMGN_CHAIN = {
    "solana": "sol",
    "ethereum": "eth",
    "bsc": "bsc",
    "base": "base",
    "avalanche": "eth",
    "monad": "monad",
}


def trade_links(token_address: str, *, chain: str = "solana") -> list[tuple[str, str]]:
    """利用可能な (label, url) のペア一覧を返す。 順序: DexScreener → UniversalX → gmgn。"""
    if not token_address:
        return []
    chain = chain.lower()
    out: list[tuple[str, str]] = []

    out.append(("DexScreener", f"https://dexscreener.com/{chain}/{token_address}"))

    chain_id = _UNIVERSALX_CHAIN_ID.get(chain)
    if chain_id:
        out.append(("UnivX", f"https://universalx.app/trade?assetId={chain_id}_{token_address}"))

    gmgn_slug = _GMGN_CHAIN.get(chain)
    if gmgn_slug:
        out.append(("gmgn", f"https://gmgn.ai/{gmgn_slug}/token/{token_address}"))

    return out


def trade_links_md(token_address: str, *, chain: str = "solana") -> str:
    """マークダウンの "[label](url) · [label](url) ..." を返す。"""
    return " · ".join(f"[{label}]({url})" for label, url in trade_links(token_address, chain=chain))


def x_search_url(query: str) -> str:
    """X (Twitter) の検索結果ページ URL。"""
    encoded = urllib.parse.quote(query)
    return f"https://x.com/search?q={encoded}&src=typed_query&f=live"


def grok_url(query: str) -> str:
    """Grok web 版にクエリを渡す URL。 ブラウザで開けば Grok チャットが起動する。"""
    encoded = urllib.parse.quote(query)
    return f"https://grok.com/?q={encoded}"


def grok_token_link_md(symbol: str | None, address: str) -> str:
    """`[Grok で調べる](url)` の md。 query は ticker + CA + ナラティブ質問。"""
    parts: list[str] = []
    if symbol:
        parts.append(f"${symbol}")
    if address:
        parts.append(f"(CA: {address})")
    parts.append("のナラティブと最新動向を教えて")
    q = " ".join(parts)
    return f"[Grok で調べる]({grok_url(q)})"


def x_search_links_md(symbol: str | None, address: str | None) -> str:
    """`[CA](...) · [$SYMBOL](...)` の md。 取れた要素のみ。"""
    parts: list[str] = []
    if address:
        parts.append(f"[CA]({x_search_url(address)})")
    if symbol:
        parts.append(f"[${symbol}]({x_search_url(f'${symbol}')})")
    return " · ".join(parts)


def solscan_token_url(token_address: str) -> str:
    return f"https://solscan.io/token/{token_address}"


def solscan_account_url(address: str) -> str:
    return f"https://solscan.io/account/{address}"
