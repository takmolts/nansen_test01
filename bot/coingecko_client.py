"""CoinGecko API ラッパ (Demo Key 推奨、無くても public 枠で動作する)。

- contract/{address} → categories
- search/trending → 現在トレンドの coin ID 一覧

Demo Plan: 10,000 call/月、 ヘッダ x-cg-demo-api-key で認証。
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoError(RuntimeError):
    pass


class CoinGeckoClient:
    def __init__(self, api_key: str | None = None, *, timeout: float = 15.0):
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "CoinGeckoClient":
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        self._session = aiohttp.ClientSession(headers=headers)
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
            if resp.status == 404:
                return None
            if resp.status >= 400:
                logger.error("CoinGecko %s -> %s: %s", path, resp.status, text[:300])
                raise CoinGeckoError(f"HTTP {resp.status}")
            try:
                return await resp.json(content_type=None)
            except Exception as e:
                raise CoinGeckoError(f"JSON parse error: {e}")

    async def get_coin_by_contract(
        self,
        platform: str,
        address: str,
    ) -> dict[str, Any] | None:
        """`/coins/{platform}/contract/{address}`. 未登録なら None。"""
        data = await self._get(f"/coins/{platform}/contract/{address}")
        return data if isinstance(data, dict) else None

    async def trending_coin_ids(self) -> list[str]:
        """`/search/trending` から coin id 一覧。"""
        data = await self._get("/search/trending")
        if not isinstance(data, dict):
            return []
        coins = data.get("coins")
        if not isinstance(coins, list):
            return []
        out: list[str] = []
        for c in coins:
            if not isinstance(c, dict):
                continue
            item = c.get("item") if isinstance(c.get("item"), dict) else c
            cid = item.get("id") if isinstance(item, dict) else None
            if isinstance(cid, str):
                out.append(cid)
        return out
