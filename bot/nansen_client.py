"""Nansen API クライアント。

memo (nansen-bot-design/memo) に貼られた実サンプルに準拠:
    - ベースパスは `/api/v1/...`
    - body はフラットな snake_case JSON
    - 認証は `apiKey:` ヘッダ

クレジット数はレスポンスヘッダから取得できない前提で、設計書の目安値を自前加算する。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


# Pro Plan 時の 1 コールあたりの目安クレジット数 (設計書より)
CREDIT_COST: dict[str, int] = {
    "token_information": 1,
    "holders": 5,
    "holders_smart_money": 5,
    "who_bought_sold": 1,
    "related_wallets": 1,
    "labels": 1,
    "transactions": 1,
    "pnl_summary": 1,
    "nansen_indicators": 1,
    "flow_intelligence": 1,
    "flows": 1,
}

API_PREFIX = "/api/v1"

# who-bought-sold の SM ラベル初期値 (必要に応じて拡張)
DEFAULT_SM_LABELS: list[str] = [
    "Smart Trader",
    "30D Smart Trader",
    "Fund",
    "Whale",
    "Public Figure",
]


class NansenAPIError(RuntimeError):
    """Nansen API 呼び出しの失敗を表す例外。"""

    def __init__(self, status: int, path: str, body: str):
        super().__init__(f"Nansen API {path} -> HTTP {status}: {body[:300]}")
        self.status = status
        self.path = path
        self.body = body


class NansenClient:
    """非同期コンテキストマネージャで使う軽量ラッパ。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        chain: str = "solana",
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._chain = chain
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self.credits_used: int = 0

    async def __aenter__(self) -> "NansenClient":
        self._session = aiohttp.ClientSession(
            headers={
                "apiKey": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _post(self, path: str, payload: dict[str, Any], credit_key: str) -> Any:
        assert self._session is not None, "NansenClient を async with で開いてから使用してください"
        url = f"{self._base_url}{API_PREFIX}{path}"
        logger.debug("POST %s payload=%s", url, payload)
        async with self._session.post(url, json=payload, timeout=self._timeout) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                logger.error("Nansen %s -> %s: %s", path, resp.status, raw[:500])
                raise NansenAPIError(resp.status, path, raw)
            self.credits_used += CREDIT_COST.get(credit_key, 0)
            logger.debug("Nansen %s -> 200: %s", path, raw[:1000])
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Nansen %s: JSON でないレスポンス: %s", path, raw[:300])
                return {"_raw": raw}

    # --- Token Godmode ---

    async def token_information(self, token_address: str, *, timeframe: str = "1d") -> Any:
        return await self._post(
            "/tgm/token-information",
            {
                "chain": self._chain,
                "token_address": token_address,
                "timeframe": timeframe,
            },
            credit_key="token_information",
        )

    async def holders_smart_money(
        self,
        token_address: str,
        *,
        per_page: int = 100,
    ) -> Any:
        """Smart Money ホルダーのみを返す (label_type=smart_money)。"""
        return await self._post(
            "/tgm/holders",
            {
                "chain": self._chain,
                "token_address": token_address,
                "aggregate_by_entity": False,
                "label_type": "smart_money",
                "pagination": {"page": 1, "per_page": per_page},
                "premium_labels": False,
                "order_by": [
                    {"field": "ownership_percentage", "direction": "DESC"},
                ],
            },
            credit_key="holders_smart_money",
        )

    async def flows(
        self,
        token_address: str,
        *,
        days: int = 7,
        label: str = "top_100_holders",
        per_page: int = 50,
    ) -> Any:
        """日別 holders_count + flow 推移 (Distribution 増加率算出に使う)。"""
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        date_to = now.strftime("%Y-%m-%dT23:59:59Z")
        return await self._post(
            "/tgm/flows",
            {
                "chain": self._chain,
                "token_address": token_address,
                "label": label,
                "date": {"from": date_from, "to": date_to},
                "pagination": {"page": 1, "per_page": per_page},
                "order_by": [
                    {"field": "date", "direction": "ASC"},
                ],
            },
            credit_key="flows",
        )

    async def holders(self, token_address: str, *, per_page: int = 100) -> Any:
        """ホルダー分布。Top 集中度とバンドル検出用 whale を拾うため per_page を多めに。

        premium_labels は Pro サブスクリプション専用なので Free プランでは false にする。
        """
        return await self._post(
            "/tgm/holders",
            {
                "chain": self._chain,
                "token_address": token_address,
                "aggregate_by_entity": False,
                "label_type": "all_holders",
                "pagination": {"page": 1, "per_page": per_page},
                "premium_labels": False,
                "order_by": [
                    {"field": "ownership_percentage", "direction": "DESC"},
                ],
            },
            credit_key="holders",
        )

    async def nansen_indicators(self, token_address: str) -> Any:
        """Nansen 独自リスク/リワード指標 (path は /tgm/indicators)。"""
        return await self._post(
            "/tgm/indicators",
            {
                "chain": self._chain,
                "token_address": token_address,
            },
            credit_key="nansen_indicators",
        )

    async def flow_intelligence(
        self,
        token_address: str,
        *,
        timeframe: str = "1d",
    ) -> Any:
        """カテゴリ別ネットフロー (CEX / Whale / Smart Trader 等)。"""
        return await self._post(
            "/tgm/flow-intelligence",
            {
                "chain": self._chain,
                "token_address": token_address,
                "timeframe": timeframe,
            },
            credit_key="flow_intelligence",
        )

    async def who_bought_sold(
        self,
        token_address: str,
        *,
        days: int = 7,
        per_page: int = 10,
        buy_or_sell: str = "BUY",
        smart_money_labels: list[str] | None = None,
    ) -> Any:
        """Smart Money の直近売買履歴。"""
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        date_to = now.strftime("%Y-%m-%dT23:59:59Z")
        labels = smart_money_labels if smart_money_labels is not None else DEFAULT_SM_LABELS
        return await self._post(
            "/tgm/who-bought-sold",
            {
                "chain": self._chain,
                "token_address": token_address,
                "buy_or_sell": buy_or_sell,
                "date": {"from": date_from, "to": date_to},
                "pagination": {"page": 1, "per_page": per_page},
                "filters": {
                    "include_smart_money_labels": labels,
                    "trade_volume_usd": {"min": 1},
                },
                "order_by": [
                    {"field": "bought_volume_usd", "direction": "DESC"},
                ],
            },
            credit_key="who_bought_sold",
        )

    # --- Profiler ---

    async def related_wallets(self, address: str, *, per_page: int = 10) -> Any:
        return await self._post(
            "/profiler/address/related-wallets",
            {
                "address": address,
                "chain": self._chain,
                "pagination": {"page": 1, "per_page": per_page},
                "order_by": [
                    {"field": "order", "direction": "ASC"},
                ],
            },
            credit_key="related_wallets",
        )

    async def labels(self, address: str, *, per_page: int = 50) -> Any:
        """address のラベル一覧を取得 (smart_money / behavioral / scam etc.)。"""
        return await self._post(
            "/profiler/address/labels",
            {
                "address": address,
                "chain": self._chain,
                "pagination": {"page": 1, "per_page": per_page},
            },
            credit_key="labels",
        )

    async def oldest_transaction(
        self,
        address: str,
        *,
        days_back: int = 365 * 10,
    ) -> Any:
        """過去 days_back 日からアカウント年齢計算用に最古 1 件の Tx を取得。"""
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")
        date_to = now.strftime("%Y-%m-%dT23:59:59Z")
        return await self._post(
            "/profiler/address/transactions",
            {
                "address": address,
                "chain": self._chain,
                "date": {"from": date_from, "to": date_to},
                "pagination": {"page": 1, "per_page": 1},
                "order_by": [
                    {"field": "block_timestamp", "direction": "ASC"},
                ],
            },
            credit_key="transactions",
        )

    async def pnl_summary(self, address: str, *, days_back: int = 365 * 10) -> Any:
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")
        date_to = now.strftime("%Y-%m-%dT23:59:59Z")
        return await self._post(
            "/profiler/address/pnl-summary",
            {
                "address": address,
                "chain": self._chain,
                "date": {"from": date_from, "to": date_to},
            },
            credit_key="pnl_summary",
        )
