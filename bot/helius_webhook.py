"""Helius Webhook 管理 REST API クライアント。

これは bot/helius_client.py (DAS RPC) とは別 API:
    GET    https://api.helius.xyz/v0/webhooks?api-key=...        一覧
    POST   https://api.helius.xyz/v0/webhooks?api-key=...        新規作成
    PUT    https://api.helius.xyz/v0/webhooks/{id}?api-key=...   更新 (全置換)
    DELETE https://api.helius.xyz/v0/webhooks/{id}?api-key=...   削除

bb_bot/main.py の同等処理 (requests / 同期) を aiohttp / 非同期に書き直したもの。
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

WEBHOOKS_BASE = "https://api.helius.xyz/v0/webhooks"


class HeliusWebhookError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Helius webhook HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class HeliusWebhookClient:
    def __init__(self, api_key: str, *, timeout: float = 30.0):
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HeliusWebhookClient":
        self._session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json"}
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _url(self, path_segment: str = "") -> str:
        if path_segment:
            return f"{WEBHOOKS_BASE}/{path_segment}?api-key={self._api_key}"
        return f"{WEBHOOKS_BASE}?api-key={self._api_key}"

    async def _request(self, method: str, url: str, *, json_body: Any = None) -> Any:
        assert self._session is not None, "async with HeliusWebhookClient(...) で開いてから使用"
        async with self._session.request(
            method, url, json=json_body, timeout=self._timeout
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.error(
                    "Helius webhook %s %s -> HTTP %s: %s",
                    method, url.split("?")[0], resp.status, text[:300],
                )
                raise HeliusWebhookError(resp.status, text)
            if not text:
                return None
            try:
                return _json.loads(text)
            except _json.JSONDecodeError:
                logger.warning("Helius webhook: JSON でないレスポンス: %s", text[:200])
                return text

    async def list_webhooks(self) -> list[dict]:
        """登録済み webhook 一覧 (account_addresses も含む)。"""
        result = await self._request("GET", self._url())
        if isinstance(result, list):
            return [w for w in result if isinstance(w, dict)]
        return []

    async def find_by_url(self, webhook_url: str) -> dict | None:
        for w in await self.list_webhooks():
            if w.get("webhookURL") == webhook_url:
                return w
        return None

    async def create_webhook(
        self,
        *,
        webhook_url: str,
        account_addresses: list[str],
        webhook_type: str = "enhanced",
        transaction_types: list[str] | None = None,
        auth_header: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "webhookURL": webhook_url,
            "webhookType": webhook_type,
            "accountAddresses": list(account_addresses),
        }
        # raw タイプでは transactionTypes は無視される
        if webhook_type == "enhanced" and transaction_types:
            payload["transactionTypes"] = list(transaction_types)
        if auth_header:
            payload["authHeader"] = auth_header
        result = await self._request("POST", self._url(), json_body=payload)
        return result if isinstance(result, dict) else {}

    async def update_webhook(
        self,
        webhook_id: str,
        *,
        webhook_url: str,
        account_addresses: list[str],
        webhook_type: str = "enhanced",
        transaction_types: list[str] | None = None,
        auth_header: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "webhookURL": webhook_url,
            "webhookType": webhook_type,
            "accountAddresses": list(account_addresses),
        }
        if webhook_type == "enhanced" and transaction_types:
            payload["transactionTypes"] = list(transaction_types)
        if auth_header:
            payload["authHeader"] = auth_header
        result = await self._request("PUT", self._url(webhook_id), json_body=payload)
        return result if isinstance(result, dict) else {}

    async def delete_webhook(self, webhook_id: str) -> bool:
        await self._request("DELETE", self._url(webhook_id))
        return True

    async def upsert_webhook(
        self,
        *,
        webhook_url: str,
        account_addresses: list[str],
        webhook_type: str = "enhanced",
        transaction_types: list[str] | None = None,
        auth_header: str | None = None,
    ) -> tuple[dict, str, dict | None]:
        """URL で既存検索 → あれば PUT、 なければ POST。

        返り値: (新しい webhook の状態, "created" | "updated", 既存 webhook (差分計算用、 created 時 None))
        """
        existing = await self.find_by_url(webhook_url)
        if existing:
            wid = existing.get("webhookID") or existing.get("webhook_id") or existing.get("id")
            if not wid:
                raise HeliusWebhookError(0, f"既存 webhook の id 取得不可: {existing}")
            updated = await self.update_webhook(
                str(wid),
                webhook_url=webhook_url,
                account_addresses=account_addresses,
                webhook_type=webhook_type,
                transaction_types=transaction_types,
                auth_header=auth_header,
            )
            return updated, "updated", existing
        created = await self.create_webhook(
            webhook_url=webhook_url,
            account_addresses=account_addresses,
            webhook_type=webhook_type,
            transaction_types=transaction_types,
            auth_header=auth_header,
        )
        return created, "created", None
