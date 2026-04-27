"""Helius DAS API ラッパ。

公開 Solana RPC では取れない情報 (creator address 等) を補完する。
無料プランでも 1コール/トークンで creator を直接取得できる。
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://mainnet.helius-rpc.com"


class HeliusError(RuntimeError):
    pass


class HeliusClient:
    def __init__(self, api_key: str, *, timeout: float = 15.0):
        self._api_key = api_key
        self._url = f"{DEFAULT_BASE}/?api-key={api_key}"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HeliusClient":
        self._session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json"}
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _post(self, payload: dict) -> Any:
        assert self._session is not None
        async with self._session.post(
            self._url, json=payload, timeout=self._timeout
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.error("Helius HTTP %s: %s", resp.status, text[:300])
                raise HeliusError(f"HTTP {resp.status}")
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                raise HeliusError(f"JSON parse error: {e}")
        if isinstance(data, dict) and "error" in data:
            logger.error("Helius RPC error: %s", data["error"])
            raise HeliusError(str(data["error"]))
        return data.get("result") if isinstance(data, dict) else None

    async def get_creator(self, mint_address: str) -> str | None:
        """getAsset の creators[0].address を返す。 取れなければ None。"""
        result = await self._post({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "getAsset",
            "params": {"id": mint_address},
        })
        if not isinstance(result, dict):
            return None
        creators = result.get("creators")
        if isinstance(creators, list) and creators:
            first = creators[0]
            if isinstance(first, dict):
                addr = first.get("address")
                if isinstance(addr, str) and addr:
                    return addr
        # creators が空のとき authorities を fallback にする
        authorities = result.get("authorities")
        if isinstance(authorities, list) and authorities:
            first = authorities[0]
            if isinstance(first, dict):
                addr = first.get("address")
                if isinstance(addr, str) and addr:
                    return addr
        return None
