from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from config import Settings
from database import SignalDatabase
from execution import ExecutionEngine
from exchange import ClosedPnlRecord, InstrumentSpec, VenuePosition
from signal_engine import RankedSignal
from state import MarketState


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _first_existing_table(
    connection: sqlite3.Connection, candidates: tuple[str, ...]
) -> str | None:
    for table_name in candidates:
        if _table_exists(connection, table_name):
            return table_name
    return None


def _columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _analytics_settings_ready(settings: Settings) -> bool:
    return hasattr(settings, "analytics_enabled")


def test_long_trade_logs_analytics_and_followthrough(tmp_path: Path) -> None:
    asyncio.run(_exercise_long_trade_analytics(tmp_path))


def test_daily_stop_loss_limit_blocks_new_long_entry(tmp_path: Path) -> None:
    asyncio.run(_exercise_daily_stop_loss_guard(tmp_path))


async def _exercise_long_trade_analytics(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "analytics.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
    )
    if not _analytics_settings_ready(settings):
        pytest.skip("analytics settings not implemented yet")

    settings.analytics_enabled = True
    settings.analytics_log_position_marks = True
    settings.analytics_log_portfolio_snapshots = True
    settings.analytics_portfolio_snapshot_on_emerging = True
    settings.analytics_post_exit_bars = 4
    settings.max_daily_stop_losses = 3

    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    entry_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in entry_actions] == [
        ("enter_long", "AAAUSDT")
    ]

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 103.10) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in exit_actions] == [
        ("exit_long", "AAAUSDT")
    ]

    follow_through_prices = (101.4, 104.2, 104.6, 103.8)
    for offset, price in enumerate(follow_through_prices, start=2):
        assert state.append_close("AAAUSDT", settings.ticker_interval_ms * offset, price) is True
        assert state.append_close(
            "BTCUSDT", settings.ticker_interval_ms * offset, 20_200.0 + offset
        ) is True
        await execution.process_cycle(
            stage="confirmed",
            cycle_time_ms=settings.ticker_interval_ms * offset,
            ranked_signals=[],
        )

    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.row_factory = sqlite3.Row
        trade_table = _first_existing_table(
            connection,
            ("trade_analytics", "trade_metrics", "trade_journal"),
        )
        assert trade_table is not None
        trade_row = connection.execute(
            f"SELECT * FROM {trade_table} WHERE ticker = ? ORDER BY rowid DESC LIMIT 1",
            ("AAAUSDT",),
        ).fetchone()
        assert trade_row is not None
        trade_columns = set(trade_row.keys())
        assert "ticker" in trade_columns
        assert "entry_price" in trade_columns or "entry_avg_price" in trade_columns
        assert "exit_price" in trade_columns or "exit_avg_price" in trade_columns
        numeric_pnl_keys = [key for key in trade_columns if "pnl" in key.lower()]
        assert numeric_pnl_keys, "expected a realized PnL field in trade analytics"
        pnl_key = numeric_pnl_keys[0]
        assert float(trade_row[pnl_key]) > 0

        assert "post_exit_favorable_excursion_pct" in trade_columns
        assert "post_exit_adverse_excursion_pct" in trade_columns
        assert "post_exit_volatility" in trade_columns
        assert float(trade_row["post_exit_favorable_excursion_pct"]) >= 0

        portfolio_table = _first_existing_table(
            connection,
            ("portfolio_snapshots", "balance_snapshots", "portfolio_state"),
        )
        assert portfolio_table is not None
        snapshot_row = connection.execute(
            f"SELECT * FROM {portfolio_table} ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert snapshot_row is not None
        snapshot_columns = set(snapshot_row.keys())
        assert any("balance" in column.lower() for column in snapshot_columns)
        assert any("open" in column.lower() for column in snapshot_columns)
        assert any("capacity" in column.lower() or "remaining" in column.lower() for column in snapshot_columns)


async def _exercise_daily_stop_loss_guard(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "analytics-daily-stop.db"),
        universe=["AAAUSDT", "BBBUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
    )
    if not _analytics_settings_ready(settings):
        pytest.skip("analytics settings not implemented yet")

    settings.analytics_enabled = True
    settings.max_daily_stop_losses = 1

    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )
    state.replace_history(
        "BBBUSDT",
        [(0, 80.0), (settings.ticker_interval_ms, 81.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    first_entry = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in first_entry] == [
        ("enter_long", "AAAUSDT")
    ]

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 98.90) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    first_exit = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in first_exit] == [
        ("exit_long", "AAAUSDT")
    ]

    follow_up = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 3,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="BBBUSDT",
                current_price=81.0,
                momentum_z=1.6,
                curvature=0.1,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    assert not any(
        action.action == "enter_long" and action.ticker == "BBBUSDT"
        for action in follow_up
    )

    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.row_factory = sqlite3.Row
        order_rows = connection.execute(
            "SELECT ticker, lifecycle, status FROM orders ORDER BY id"
        ).fetchall()
        assert not any(row["ticker"] == "BBBUSDT" for row in order_rows)
