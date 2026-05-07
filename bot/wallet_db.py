"""digest 連動の wallet 出現履歴を SQLite に蓄積する。

スキーマ:
- wallet_appearances: 1 token × 1 wallet × 1 検出日時の生レコード
- 集計は wallet_summary view で動的算出
- sm_roster: Smart Money DEX trades 由来の Helius 監視候補ロスター
  (1 wallet 1 行、 日次 upsert で観測カウント増加)
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

            CREATE TABLE IF NOT EXISTS sm_roster (
                wallet_address TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                total_observations INTEGER NOT NULL DEFAULT 1,
                last_label TEXT,
                last_trade_count_24h INTEGER,
                last_trade_sum_usd REAL,
                last_trade_max_usd REAL,
                last_bought_top TEXT,
                helius_registered INTEGER NOT NULL DEFAULT 0,
                helius_registered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sm_roster_last_seen
                ON sm_roster(last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_sm_roster_helius
                ON sm_roster(helius_registered);
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

    # --- SM roster (Helius 登録候補管理) ---

    async def upsert_sm_roster(self, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """SM dex-trades 集計済 roster 行を upsert する。

        各行は scripts/probe_sm_roster.py の _aggregate_roster と同形:
            wallet_address, trade_count, sum_trade_value_usd, max_trade_value_usd,
            label, bought_tokens (defaultdict[str, int]), last_seen, ...

        返り値は (新規 insert 件数, 更新件数)。
        """
        assert self._conn is not None
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        inserted = 0
        updated = 0
        for r in rows:
            wallet = r.get("wallet_address")
            if not isinstance(wallet, str) or not wallet:
                continue
            label = r.get("label") or None
            trade_count = r.get("trade_count")
            sum_usd = r.get("sum_trade_value_usd")
            max_usd = r.get("max_trade_value_usd")

            bought = r.get("bought_tokens") or {}
            if isinstance(bought, dict) and bought:
                bought_top = ",".join(
                    f"{sym}:{n}"
                    for sym, n in sorted(bought.items(), key=lambda x: -x[1])[:5]
                )
            else:
                bought_top = None

            async with self._conn.execute(
                "SELECT 1 FROM sm_roster WHERE wallet_address = ?",
                (wallet,),
            ) as cur:
                existed = await cur.fetchone()

            if existed:
                await self._conn.execute(
                    """
                    UPDATE sm_roster SET
                        last_seen_at = ?,
                        total_observations = total_observations + 1,
                        last_label = COALESCE(?, last_label),
                        last_trade_count_24h = ?,
                        last_trade_sum_usd = ?,
                        last_trade_max_usd = ?,
                        last_bought_top = ?
                    WHERE wallet_address = ?
                    """,
                    (
                        now_iso,
                        label,
                        trade_count,
                        sum_usd,
                        max_usd,
                        bought_top,
                        wallet,
                    ),
                )
                updated += 1
            else:
                await self._conn.execute(
                    """
                    INSERT INTO sm_roster (
                        wallet_address, first_seen_at, last_seen_at, total_observations,
                        last_label, last_trade_count_24h, last_trade_sum_usd,
                        last_trade_max_usd, last_bought_top
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        wallet,
                        now_iso,
                        now_iso,
                        label,
                        trade_count,
                        sum_usd,
                        max_usd,
                        bought_top,
                    ),
                )
                inserted += 1
        await self._conn.commit()
        return inserted, updated

    async def sm_roster_count(self) -> tuple[int, int]:
        """(全件, helius 未登録件数) を返す。"""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN helius_registered = 0 THEN 1 ELSE 0 END) FROM sm_roster"
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return 0, 0
            return int(row[0] or 0), int(row[1] or 0)

    async def list_sm_roster(
        self,
        *,
        order_by: str = "last_seen_at",
        limit: int = 50,
        only_unregistered: bool = False,
    ) -> list[aiosqlite.Row]:
        """roster を一覧取得。 order_by は last_seen_at / total_observations / last_trade_sum_usd。"""
        assert self._conn is not None
        if order_by not in ("last_seen_at", "total_observations", "last_trade_sum_usd"):
            order_by = "last_seen_at"
        where = "WHERE helius_registered = 0" if only_unregistered else ""
        sql = f"""
        SELECT wallet_address, first_seen_at, last_seen_at, total_observations,
               last_label, last_trade_count_24h, last_trade_sum_usd,
               last_trade_max_usd, last_bought_top,
               helius_registered, helius_registered_at
        FROM sm_roster
        {where}
        ORDER BY {order_by} DESC
        LIMIT ?
        """
        async with self._conn.execute(sql, (limit,)) as cursor:
            return await cursor.fetchall()

    async def list_unregistered_sm_wallets(self) -> list[str]:
        """Helius 未登録の wallet_address のみを返す。"""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT wallet_address FROM sm_roster WHERE helius_registered = 0"
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def prune_sm_roster(self, max_count: int) -> int:
        """sm_roster が max_count を超えていれば、 古い観測順に超過分を削除する。

        eviction policy: ORDER BY last_seen_at ASC, total_observations ASC
            → 最後に観測されたのが古く、 観測回数も少ない wallet から優先削除。
            recency-first だが同日 last_seen なら observation count で tie-break。

        max_count <= 0 のときは何もせず 0 を返す (無制限扱い)。
        返り値は削除件数。
        """
        assert self._conn is not None
        if max_count <= 0:
            return 0
        async with self._conn.execute("SELECT COUNT(*) FROM sm_roster") as cur:
            row = await cur.fetchone()
            total = int(row[0]) if row else 0
        if total <= max_count:
            return 0
        over = total - max_count
        async with self._conn.execute(
            """
            SELECT wallet_address FROM sm_roster
            ORDER BY last_seen_at ASC, total_observations ASC
            LIMIT ?
            """,
            (over,),
        ) as cur:
            victim_rows = await cur.fetchall()
        victims = [r[0] for r in victim_rows]
        if not victims:
            return 0
        placeholders = ",".join("?" for _ in victims)
        await self._conn.execute(
            f"DELETE FROM sm_roster WHERE wallet_address IN ({placeholders})",
            victims,
        )
        await self._conn.commit()
        logger.info(
            "sm_roster prune: 上限 %d 超過 → %d 件削除 (oldest last_seen)",
            max_count, len(victims),
        )
        return len(victims)

    async def list_all_sm_wallets(self) -> list[str]:
        """sm_roster の全 wallet_address (登録済 + 未登録)。 Helius PUT は全置換なので毎回 full set 必要。"""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT wallet_address FROM sm_roster ORDER BY wallet_address"
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def mark_sm_helius_registered(self, wallets: Iterable[str]) -> int:
        """Helius 登録済みフラグを立てる。 件数を返す。"""
        assert self._conn is not None
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cnt = 0
        for w in wallets:
            if not isinstance(w, str) or not w:
                continue
            await self._conn.execute(
                """
                UPDATE sm_roster
                SET helius_registered = 1, helius_registered_at = ?
                WHERE wallet_address = ? AND helius_registered = 0
                """,
                (now_iso, w),
            )
            cnt += 1
        await self._conn.commit()
        return cnt
