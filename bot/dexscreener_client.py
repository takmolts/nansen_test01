"""DexScreener API ラッパ (無料・APIキー不要)。

- 類似トークン検索 (αβ判定用)
- ブースト中トークン一覧
レート制限は約 300 req/min なので /check 1回で問題なし。
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"


class DexScreenerError(RuntimeError):
    pass


class DexScreenerClient:
    def __init__(self, *, timeout: float = 15.0):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "DexScreenerClient":
        self._session = aiohttp.ClientSession(headers={"Accept": "application/json"})
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str) -> Any:
        assert self._session is not None
        url = f"{BASE_URL}{path}"
        logger.debug("GET %s", url)
        async with self._session.get(url, timeout=self._timeout) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.error("DexScreener %s -> %s: %s", path, resp.status, text[:300])
                raise DexScreenerError(f"HTTP {resp.status}")
            try:
                return await resp.json(content_type=None)
            except Exception as e:
                raise DexScreenerError(f"JSON parse error: {e}")

    async def search(self, query: str) -> list[dict[str, Any]]:
        """シンボルなどで類似トークンを検索。 pairs[] を返す。"""
        data = await self._get(f"/latest/dex/search?q={query}")
        if not isinstance(data, dict):
            return []
        pairs = data.get("pairs")
        return [p for p in pairs if isinstance(p, dict)] if isinstance(pairs, list) else []

    async def is_boosted(self, token_address: str) -> bool:
        """対象トークンが現在 Boost されているか。"""
        data = await self._get("/token-boosts/latest/v1")
        if not isinstance(data, list):
            return False
        addr_lower = token_address.lower()
        for item in data:
            if not isinstance(item, dict):
                continue
            ta = item.get("tokenAddress")
            if isinstance(ta, str) and ta.lower() == addr_lower:
                return True
        return False
