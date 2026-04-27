"""Solana JSON-RPC ラッパ (Deployer Trust の判定材料を取るため)。

mintAuthority がある場合 → そのアドレスを deployer 扱いして Nansen profiler に流す
mintAuthority が null の場合 → renounce 済みとして安全シグナル扱い
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"


class SolanaRPCError(RuntimeError):
    pass


class SolanaRPCClient:
    def __init__(self, base_url: str | None = None, *, timeout: float = 15.0):
        self._base_url = (base_url or DEFAULT_RPC_URL).rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "SolanaRPCClient":
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
            self._base_url, json=payload, timeout=self._timeout
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.error("Solana RPC HTTP %s: %s", resp.status, text[:300])
                raise SolanaRPCError(f"HTTP {resp.status}: {text[:200]}")
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                raise SolanaRPCError(f"JSON parse error: {e}")
        if isinstance(data, dict) and "error" in data:
            logger.error("Solana RPC error: %s", data["error"])
            raise SolanaRPCError(str(data["error"]))
        return data.get("result") if isinstance(data, dict) else None

    async def get_mint_info(self, mint_address: str) -> dict[str, Any] | None:
        """SPL Token Mint アカウントの parsed info を返す。"""
        result = await self._post({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [mint_address, {"encoding": "jsonParsed"}],
        })
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            return None
        data = value.get("data")
        if not isinstance(data, dict):
            return None
        parsed = data.get("parsed")
        if not isinstance(parsed, dict):
            return None
        info = parsed.get("info")
        return info if isinstance(info, dict) else None

    async def fetch_mint_authority(self, mint_address: str) -> tuple[bool, str | None]:
        """(fetched, authority) を返す。

        - fetched=True, authority=str → mint authority 保有者
        - fetched=True, authority=None → 真の renounce (mint authority null)
        - fetched=False, authority=None → RPC 取得失敗
        """
        try:
            info = await self.get_mint_info(mint_address)
        except SolanaRPCError:
            return False, None
        except Exception:
            logger.exception("Solana RPC 例外")
            return False, None

        if info is None:
            return False, None
        auth = info.get("mintAuthority")
        if isinstance(auth, str) and auth:
            return True, auth
        return True, None
