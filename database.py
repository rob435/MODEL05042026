from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SignalRecord:
    timestamp: str
    stage: str
    signal_kind: str
    ticker: str
    momentum_z: float
    curvature: float
    hurst: float
    regime_score: int
    composite_score: float
    alerted: bool
    price: float
    rank: int
    persistence_hits: int
    dom_falling: bool
    dom_state: str
    dom_change_pct: float


class SignalDatabase:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'confirmed',
                signal_kind TEXT NOT NULL DEFAULT 'confirmed',
                ticker TEXT NOT NULL,
                momentum_z REAL NOT NULL,
                curvature REAL NOT NULL,
                hurst REAL NOT NULL,
                regime_score INTEGER NOT NULL,
                composite_score REAL NOT NULL,
                alerted INTEGER NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                rank INTEGER NOT NULL DEFAULT 0,
                persistence_hits INTEGER NOT NULL DEFAULT 0,
                dom_falling INTEGER NOT NULL DEFAULT 0,
                dom_state TEXT NOT NULL DEFAULT 'neutral',
                dom_change_pct REAL NOT NULL DEFAULT 0
            )
            """
        )
        existing_columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(signals)")
        }
        if "stage" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN stage TEXT NOT NULL DEFAULT 'confirmed'"
            )
        if "signal_kind" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN signal_kind TEXT NOT NULL DEFAULT 'confirmed'"
            )
        if "price" not in existing_columns:
            self._conn.execute("ALTER TABLE signals ADD COLUMN price REAL NOT NULL DEFAULT 0")
        if "rank" not in existing_columns:
            self._conn.execute("ALTER TABLE signals ADD COLUMN rank INTEGER NOT NULL DEFAULT 0")
        if "persistence_hits" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN persistence_hits INTEGER NOT NULL DEFAULT 0"
            )
        if "dom_falling" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN dom_falling INTEGER NOT NULL DEFAULT 0"
            )
        if "dom_state" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN dom_state TEXT NOT NULL DEFAULT 'neutral'"
            )
        if "dom_change_pct" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN dom_change_pct REAL NOT NULL DEFAULT 0"
            )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summary_cycles (
                stage TEXT NOT NULL,
                cycle_time_ms INTEGER NOT NULL,
                PRIMARY KEY (stage, cycle_time_ms)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL UNIQUE,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                signal_kind TEXT NOT NULL,
                requested_qty REAL NOT NULL,
                requested_notional_usd REAL NOT NULL,
                requested_price REAL NOT NULL,
                fill_price REAL,
                venue TEXT NOT NULL DEFAULT 'bybit',
                is_demo INTEGER NOT NULL DEFAULT 1,
                external_order_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                filled_at TEXT,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                confirmed_at TEXT,
                entry_order_id INTEGER NOT NULL,
                exit_order_id INTEGER,
                entry_signal_kind TEXT NOT NULL,
                confirmation_signal_kind TEXT,
                quantity REAL NOT NULL,
                notional_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (entry_order_id) REFERENCES orders(id),
                FOREIGN KEY (exit_order_id) REFERENCES orders(id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_ticker_timestamp
            ON signals (ticker, timestamp)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_ticker_status
            ON orders (ticker, status, lifecycle)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_positions_ticker_status
            ON positions (ticker, status)
            """
        )
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_open_ticker
            ON positions (ticker)
            WHERE status = 'open'
            """
        )
        self._conn.commit()

    async def log_signal(self, record: SignalRecord) -> None:
        await asyncio.to_thread(self._log_signal_sync, record)

    async def log_signals(self, records: list[SignalRecord]) -> None:
        if not records:
            return
        await asyncio.to_thread(self._log_signals_sync, records)

    def _log_signal_sync(self, record: SignalRecord) -> None:
        self._log_signals_sync([record])

    def _log_signals_sync(self, records: list[SignalRecord]) -> None:
        self._conn.execute(
            "BEGIN"
        )
        self._conn.executemany(
            """
            INSERT INTO signals (
                timestamp,
                stage,
                signal_kind,
                ticker,
                momentum_z,
                curvature,
                hurst,
                regime_score,
                composite_score,
                alerted,
                price,
                rank,
                persistence_hits,
                dom_falling,
                dom_state,
                dom_change_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.timestamp,
                    record.stage,
                    record.signal_kind,
                    record.ticker,
                    record.momentum_z,
                    record.curvature,
                    record.hurst,
                    record.regime_score,
                    record.composite_score,
                    int(record.alerted),
                    record.price,
                    record.rank,
                    record.persistence_hits,
                    int(record.dom_falling),
                    record.dom_state,
                    record.dom_change_pct,
                )
                for record in records
            ],
        )
        self._conn.commit()

    async def record_summary_cycle(self, stage: str, cycle_time_ms: int) -> bool:
        return await asyncio.to_thread(self._record_summary_cycle_sync, stage, cycle_time_ms)

    def _record_summary_cycle_sync(self, stage: str, cycle_time_ms: int) -> bool:
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO summary_cycles (stage, cycle_time_ms)
            VALUES (?, ?)
            """,
            (stage, cycle_time_ms),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    async def create_order(
        self,
        *,
        client_order_id: str,
        ticker: str,
        side: str,
        order_type: str,
        lifecycle: str,
        status: str,
        stage: str,
        signal_kind: str,
        requested_qty: float,
        requested_notional_usd: float,
        requested_price: float,
        venue: str,
        is_demo: bool,
        created_at: str,
        updated_at: str,
        fill_price: float | None = None,
        external_order_id: str | None = None,
        filled_at: str | None = None,
        notes: str = "",
    ) -> int:
        return await asyncio.to_thread(
            self._create_order_sync,
            client_order_id,
            ticker,
            side,
            order_type,
            lifecycle,
            status,
            stage,
            signal_kind,
            requested_qty,
            requested_notional_usd,
            requested_price,
            venue,
            is_demo,
            created_at,
            updated_at,
            fill_price,
            external_order_id,
            filled_at,
            notes,
        )

    def _create_order_sync(
        self,
        client_order_id: str,
        ticker: str,
        side: str,
        order_type: str,
        lifecycle: str,
        status: str,
        stage: str,
        signal_kind: str,
        requested_qty: float,
        requested_notional_usd: float,
        requested_price: float,
        venue: str,
        is_demo: bool,
        created_at: str,
        updated_at: str,
        fill_price: float | None,
        external_order_id: str | None,
        filled_at: str | None,
        notes: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO orders (
                client_order_id,
                ticker,
                side,
                order_type,
                lifecycle,
                status,
                stage,
                signal_kind,
                requested_qty,
                requested_notional_usd,
                requested_price,
                fill_price,
                venue,
                is_demo,
                external_order_id,
                created_at,
                updated_at,
                filled_at,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_order_id,
                ticker,
                side,
                order_type,
                lifecycle,
                status,
                stage,
                signal_kind,
                requested_qty,
                requested_notional_usd,
                requested_price,
                fill_price,
                venue,
                int(is_demo),
                external_order_id,
                created_at,
                updated_at,
                filled_at,
                notes,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    async def get_active_order(self, ticker: str, lifecycle: str) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._get_active_order_sync, ticker, lifecycle)

    def _get_active_order_sync(self, ticker: str, lifecycle: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT *
            FROM orders
            WHERE ticker = ?
              AND lifecycle = ?
              AND status IN ('created', 'submitted')
            ORDER BY id DESC
            LIMIT 1
            """,
            (ticker, lifecycle),
        ).fetchone()

    async def open_position(
        self,
        *,
        ticker: str,
        opened_at: str,
        updated_at: str,
        entry_order_id: int,
        entry_signal_kind: str,
        quantity: float,
        notional_usd: float,
        entry_price: float,
        notes: str = "",
    ) -> int:
        return await asyncio.to_thread(
            self._open_position_sync,
            ticker,
            opened_at,
            updated_at,
            entry_order_id,
            entry_signal_kind,
            quantity,
            notional_usd,
            entry_price,
            notes,
        )

    def _open_position_sync(
        self,
        ticker: str,
        opened_at: str,
        updated_at: str,
        entry_order_id: int,
        entry_signal_kind: str,
        quantity: float,
        notional_usd: float,
        entry_price: float,
        notes: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO positions (
                ticker,
                status,
                opened_at,
                updated_at,
                entry_order_id,
                entry_signal_kind,
                quantity,
                notional_usd,
                entry_price,
                notes
            )
            VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                opened_at,
                updated_at,
                entry_order_id,
                entry_signal_kind,
                quantity,
                notional_usd,
                entry_price,
                notes,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    async def get_open_position(self, ticker: str) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._get_open_position_sync, ticker)

    def _get_open_position_sync(self, ticker: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT *
            FROM positions
            WHERE ticker = ?
              AND status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (ticker,),
        ).fetchone()

    async def list_open_positions(self) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._list_open_positions_sync)

    def _list_open_positions_sync(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT *
            FROM positions
            WHERE status = 'open'
            ORDER BY id ASC
            """
        ).fetchall()

    async def confirm_position(
        self,
        position_id: int,
        confirmation_signal_kind: str,
        confirmed_at: str,
        updated_at: str,
        notes: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._confirm_position_sync,
            position_id,
            confirmation_signal_kind,
            confirmed_at,
            updated_at,
            notes,
        )

    def _confirm_position_sync(
        self,
        position_id: int,
        confirmation_signal_kind: str,
        confirmed_at: str,
        updated_at: str,
        notes: str,
    ) -> None:
        self._conn.execute(
            """
            UPDATE positions
            SET confirmation_signal_kind = ?,
                confirmed_at = ?,
                updated_at = ?,
                notes = CASE
                    WHEN ? = '' THEN notes
                    WHEN notes = '' THEN ?
                    ELSE notes || ' | ' || ?
                END
            WHERE id = ?
            """,
            (
                confirmation_signal_kind,
                confirmed_at,
                updated_at,
                notes,
                notes,
                notes,
                position_id,
            ),
        )
        self._conn.commit()

    async def close_position(
        self,
        position_id: int,
        exit_order_id: int,
        exit_price: float,
        closed_at: str,
        updated_at: str,
        notes: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._close_position_sync,
            position_id,
            exit_order_id,
            exit_price,
            closed_at,
            updated_at,
            notes,
        )

    def _close_position_sync(
        self,
        position_id: int,
        exit_order_id: int,
        exit_price: float,
        closed_at: str,
        updated_at: str,
        notes: str,
    ) -> None:
        self._conn.execute(
            """
            UPDATE positions
            SET status = 'closed',
                exit_order_id = ?,
                exit_price = ?,
                closed_at = ?,
                updated_at = ?,
                notes = CASE
                    WHEN ? = '' THEN notes
                    WHEN notes = '' THEN ?
                    ELSE notes || ' | ' || ?
                END
            WHERE id = ?
            """,
            (
                exit_order_id,
                exit_price,
                closed_at,
                updated_at,
                notes,
                notes,
                notes,
                position_id,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
