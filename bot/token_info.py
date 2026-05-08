"""DexScreener から取得した token info の TTL キャッシュ付きルックアップ。

sm_signal (リアルタイム) と sm_summary (毎時集計) の両方から同じ mint を
何度も問い合わせるため、 モジュールレベルで共有キャッシュを持つ。
TTL は短すぎると DexScreener を叩きすぎるし、 長いと mcap 等が古くなる。
10 分は妥当な妥協点。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from bot.dexscreener_client import DexScreenerClient

logger = logging.getLogger(__name__)

_TTL_SEC = 10 * 60  # 10 分


@dataclass
class TokenInfo:
    address: str
    symbol: str | None
    name: str | None
    market_cap: float | None
    price_usd: float | None
    image_url: str | None
    fetched_at: float

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.fetched_at) > _TTL_SEC


_cache: dict[str, TokenInfo] = {}
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(addr: str) -> asyncio.Lock:
    lock = _locks.get(addr)
    if lock is None:
        lock = asyncio.Lock()
        _locks[addr] = lock
    return lock


def _coerce_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def get_token_info(address: str, *, force: bool = False) -> TokenInfo | None:
    """address の token info を返す。 cache 有効内なら API を叩かない。

    fetch 失敗時はキャッシュがあればそれを返し、 無ければ None。
    """
    if not isinstance(address, str) or not address:
        return None
    cached = _cache.get(address)
    if cached and not cached.is_stale and not force:
        return cached

    async with _get_lock(address):
        # 二重 fetch ガード (lock 待ち中に他 coroutine が埋めたかも)
        cached = _cache.get(address)
        if cached and not cached.is_stale and not force:
            return cached

        try:
            async with DexScreenerClient() as ds:
                data = await ds.get_token_data(address)
        except Exception:
            logger.warning(
                "token_info: DexScreener 取得失敗 addr=%s", address, exc_info=True
            )
            return cached

        if not isinstance(data, dict):
            return cached

        base = data.get("baseToken") if isinstance(data.get("baseToken"), dict) else None
        info = data.get("info") if isinstance(data.get("info"), dict) else None

        symbol = base.get("symbol") if isinstance(base, dict) else None
        name = base.get("name") if isinstance(base, dict) else None
        mcap = data.get("marketCap")
        if mcap is None:
            mcap = data.get("fdv")
        img = info.get("imageUrl") if isinstance(info, dict) else None

        tinfo = TokenInfo(
            address=address,
            symbol=symbol if isinstance(symbol, str) and symbol else None,
            name=name if isinstance(name, str) and name else None,
            market_cap=_coerce_float(mcap),
            price_usd=_coerce_float(data.get("priceUsd")),
            image_url=img if isinstance(img, str) and img.startswith("http") else None,
            fetched_at=time.time(),
        )
        _cache[address] = tinfo
        return tinfo


async def get_token_infos(addresses: list[str]) -> dict[str, TokenInfo | None]:
    """複数 mint を並列で取得 (cache + DexScreener)。 dict[address, TokenInfo|None]。"""
    if not addresses:
        return {}
    results = await asyncio.gather(
        *(get_token_info(a) for a in addresses), return_exceptions=False
    )
    return dict(zip(addresses, results))
