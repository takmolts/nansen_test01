"""digest 連動の wallet 出現履歴を SQLite に蓄積する。

スキーマ:
- wallet_appearances: 1 token × 1 wallet × 1 検出日時の生レコード
- 集計は wallet_summary view で動的算出
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/wallets.db"


class WalletDB:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self._path = path
        Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "WalletDB":
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._init_schema()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wallet_appearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                pnl_usd_realised REAL,
                pnl_usd_unrealised REAL,
                pnl_usd_total REAL,
                nof_trades INTEGER,
                label TEXT,
                detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_wallet_appearances_wallet
                ON wallet_appearances(wallet_address);
            CREATE INDEX IF NOT EXISTS idx_wallet_appearances_token
                ON wallet_appearances(token_address);
            CREATE INDEX IF NOT EXISTS idx_wallet_appearances_detected
                ON wallet_appearances(detected_at);
            """
        )
        await self._conn.commit()

    async def insert_appearances(self, rows: Iterable[dict[str, Any]]) -> int:
        """pnl-leaderboard レコードを bulk insert する。 件数を返す。"""
        assert self._conn is not None
        params = []
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in rows:
            wallet = r.get("wallet_address")
            token = r.get("token_address")
            if not wallet or not token:
                continue
            params.append((
                wallet,
                token,
                r.get("token_symbol"),
                r.get("pnl_usd_realised"),
                r.get("pnl_usd_unrealised"),
                r.get("pnl_usd_total"),
                r.get("nof_trades"),
                r.get("label"),
                now_iso,
            ))
        if not params:
            return 0
        await self._conn.executemany(
            """
            INSERT INTO wallet_appearances (
                wallet_address, token_address, token_symbol,
                pnl_usd_realised, pnl_usd_unrealised, pnl_usd_total,
                nof_trades, label, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        await self._conn.commit()
        return len(params)

    async def top_wallets(
        self,
        *,
        order_by: str = "unique_tokens",
        limit: int = 20,
        min_pnl: float = 0.0,
    ) -> list[aiosqlite.Row]:
        """集計済 wallet 上位を返す。 order_by: unique_tokens / sum_pnl_usd / total_appearances。"""
        assert self._conn is not None
        if order_by not in ("unique_tokens", "sum_pnl_usd", "total_appearances"):
            order_by = "unique_tokens"
        sql = f"""
        SELECT
            wallet_address,
            MAX(label) AS label,
            COUNT(DISTINCT token_address) AS unique_tokens,
            COUNT(*) AS total_appearances,
            COALESCE(SUM(pnl_usd_total), 0) AS sum_pnl_usd,
            COALESCE(AVG(pnl_usd_total), 0) AS avg_pnl_usd,
            MAX(detected_at) AS last_seen
        FROM wallet_appearances
        GROUP BY wallet_address
        HAVING sum_pnl_usd >= ?
        ORDER BY {order_by} DESC, sum_pnl_usd DESC
        LIMIT ?
        """
        async with self._conn.execute(sql, (min_pnl, limit)) as cursor:
            return await cursor.fetchall()

    async def total_count(self) -> int:
        """蓄積されたレコード総数。"""
        assert self._conn is not None
        async with self._conn.execute("SELECT COUNT(*) FROM wallet_appearances") as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
