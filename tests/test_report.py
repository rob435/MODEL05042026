from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from report import format_report, load_report_summary


def test_load_report_summary_aggregates_signal_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "signals.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE signals (
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                signal_kind TEXT NOT NULL,
                ticker TEXT NOT NULL,
                momentum_z REAL NOT NULL,
                curvature REAL NOT NULL,
                hurst REAL NOT NULL,
                regime_score INTEGER NOT NULL,
                composite_score REAL NOT NULL,
                alerted INTEGER NOT NULL,
                price REAL NOT NULL,
                rank INTEGER NOT NULL,
                dom_falling INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO signals (
                timestamp, stage, signal_kind, ticker, momentum_z, curvature, hurst,
                regime_score, composite_score, alerted, price, rank, dom_falling
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-04-03T00:00:00+00:00", "confirmed", "confirmed", "AAAUSDT", 1.0, 0.1, 0.6, 3, 2.1, 1, 100.0, 1, 1),
                ("2026-04-03T00:15:00+00:00", "emerging", "watchlist", "AAAUSDT", 0.8, 0.1, 0.6, 3, 1.8, 0, 101.0, 2, 1),
                ("2026-04-03T00:15:00+00:00", "emerging", "none", "BBBUSDT", 0.2, 0.0, 0.5, 3, 0.4, 0, 50.0, 8, 1),
            ],
        )
        connection.commit()

    summary = load_report_summary(str(database_path), top_n=2)

    assert summary.total_rows == 3
    assert summary.alerted_rows == 1
    assert summary.first_timestamp == "2026-04-03T00:00:00+00:00"
    assert summary.last_timestamp == "2026-04-03T00:15:00+00:00"
    assert summary.stage_counts == [("confirmed", 1, 1), ("emerging", 2, 0)]
    assert summary.signal_kind_counts == [("confirmed", 1, 1), ("none", 1, 0), ("watchlist", 1, 0)]
    assert summary.top_tickers[0][0] == "AAAUSDT"
    assert summary.top_tickers[0][1] == 2
    assert summary.top_tickers[0][2] == 1


def test_format_report_renders_expected_sections() -> None:
    rendered = format_report(
        type("Summary", (), {
            "total_rows": 2,
            "alerted_rows": 1,
            "first_timestamp": "a",
            "last_timestamp": "b",
            "stage_counts": [("confirmed", 1, 1), ("emerging", 1, 0)],
            "signal_kind_counts": [("confirmed", 1, 1), ("watchlist", 1, 0)],
            "top_tickers": [("AAAUSDT", 2, 1, 1.25, 2.5)],
        })()
    )

    assert "Rows: 2" in rendered
    assert "Alerted rows: 1" in rendered
    assert "emerging: rows=1 alerts=0" in rendered
    assert "watchlist: rows=1 alerts=0" in rendered
    assert "AAAUSDT: rows=2 alerts=1" in rendered
    assert rendered.index("Stage rows:") < rendered.index("  confirmed: rows=1 alerts=1")
    assert rendered.index("Signal kinds:") < rendered.index("  watchlist: rows=1 alerts=0")
    assert rendered.index("Top tickers:") < rendered.index("  AAAUSDT: rows=2 alerts=1 avg_composite=1.250 max_composite=2.500")


def test_load_report_summary_aggregates_closed_trades_by_day_from_positions(tmp_path: Path) -> None:
    database_path = tmp_path / "positions.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE signals (
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                signal_kind TEXT NOT NULL,
                ticker TEXT NOT NULL,
                momentum_z REAL NOT NULL,
                curvature REAL NOT NULL,
                hurst REAL NOT NULL,
                regime_score INTEGER NOT NULL,
                composite_score REAL NOT NULL,
                alerted INTEGER NOT NULL,
                price REAL NOT NULL,
                rank INTEGER NOT NULL,
                dom_falling INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                entry_order_id INTEGER NOT NULL,
                exit_order_id INTEGER,
                entry_signal_kind TEXT NOT NULL,
                confirmation_signal_kind TEXT,
                quantity REAL NOT NULL,
                notional_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.executemany(
            "INSERT INTO orders (id, ticker, side, lifecycle, status) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "AAAUSDT", "Sell", "entry", "filled"),
                (2, "BBBUSDT", "Sell", "entry", "filled"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO positions (
                id, ticker, status, opened_at, updated_at, closed_at, entry_order_id,
                exit_order_id, entry_signal_kind, confirmation_signal_kind, quantity,
                notional_usd, entry_price, exit_price, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "AAAUSDT",
                    "closed",
                    "2026-04-03T00:00:00+00:00",
                    "2026-04-03T02:00:00+00:00",
                    "2026-04-03T02:00:00+00:00",
                    1,
                    10,
                    "entry_ready",
                    "confirmed",
                    10.0,
                    1000.0,
                    100.0,
                    97.5,
                    "take_profit_hit",
                ),
                (
                    2,
                    "BBBUSDT",
                    "closed",
                    "2026-04-04T00:00:00+00:00",
                    "2026-04-04T03:00:00+00:00",
                    "2026-04-04T03:00:00+00:00",
                    2,
                    11,
                    "entry_ready",
                    "confirmed_strong",
                    8.0,
                    800.0,
                    100.0,
                    103.0,
                    "stop_loss_hit",
                ),
            ],
        )
        connection.commit()

    summary = load_report_summary(str(database_path), top_n=2)

    assert summary.total_rows == 0
    assert summary.trade_overview is not None
    assert summary.trade_overview.trade_count == 2
    assert summary.trade_overview.wins == 1
    assert summary.trade_overview.losses == 1
    assert summary.trade_overview.take_profits == 1
    assert summary.trade_overview.stop_losses == 1
    assert summary.trade_overview.avg_notional_usd == 900.0
    assert [row.day for row in summary.trade_daily] == ["2026-04-03", "2026-04-04"]
    assert summary.trade_daily[0].wins == 1
    assert summary.trade_daily[1].losses == 1
    assert summary.portfolio_overview is None

    rendered = format_report(summary)
    assert "Trade analytics:" in rendered
    assert "Daily trade results:" in rendered
    assert "2026-04-03: trades=1 wins=1 losses=0" in rendered
    assert "2026-04-04: trades=1 wins=0 losses=1" in rendered


def test_load_report_summary_can_export_raw_signals_when_requested(tmp_path: Path) -> None:
    database_path = tmp_path / "signals.sqlite3"
    export_dir = tmp_path / "exports"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE signals (
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                signal_kind TEXT NOT NULL,
                ticker TEXT NOT NULL,
                momentum_z REAL NOT NULL,
                curvature REAL NOT NULL,
                hurst REAL NOT NULL,
                regime_score INTEGER NOT NULL,
                composite_score REAL NOT NULL,
                alerted INTEGER NOT NULL,
                price REAL NOT NULL,
                rank INTEGER NOT NULL,
                dom_falling INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO signals (
                timestamp, stage, signal_kind, ticker, momentum_z, curvature, hurst,
                regime_score, composite_score, alerted, price, rank, dom_falling
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-04-09T00:00:00+00:00",
                "confirmed",
                "confirmed",
                "AAAUSDT",
                1.0,
                0.1,
                0.6,
                3,
                2.1,
                1,
                100.0,
                1,
                1,
            ),
        )
        connection.commit()

    load_report_summary(
        str(database_path),
        export_dir=str(export_dir),
        include_signals_export=True,
    )

    assert (export_dir / "signals.csv").exists()


def test_load_report_summary_infers_venue_tp_and_sl_from_position_notes(tmp_path: Path) -> None:
    database_path = tmp_path / "positions.sqlite3"
    export_dir = tmp_path / "exports"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                entry_order_id INTEGER NOT NULL,
                exit_order_id INTEGER,
                entry_signal_kind TEXT NOT NULL,
                confirmation_signal_kind TEXT,
                quantity REAL NOT NULL,
                notional_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO positions (
                id, ticker, status, opened_at, updated_at, closed_at, entry_order_id,
                exit_order_id, entry_signal_kind, confirmation_signal_kind, quantity,
                notional_usd, entry_price, exit_price, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "AAAUSDT",
                    "closed",
                    "2026-04-09T00:00:00+00:00",
                    "2026-04-09T01:00:00+00:00",
                    "2026-04-09T01:00:00+00:00",
                    1,
                    10,
                    "entry_ready",
                    "confirmed",
                    10.0,
                    1000.0,
                    100.0,
                    97.0,
                    "Bybit live demo order placed. Venue TP=97.0 SL=102.0. | closed on Bybit venue",
                ),
                (
                    2,
                    "BBBUSDT",
                    "closed",
                    "2026-04-09T02:00:00+00:00",
                    "2026-04-09T03:00:00+00:00",
                    "2026-04-09T03:00:00+00:00",
                    2,
                    11,
                    "entry_ready",
                    "confirmed",
                    10.0,
                    1000.0,
                    100.0,
                    102.0,
                    "Bybit live demo order placed. Venue TP=97.0 SL=102.0. | closed on Bybit venue",
                ),
            ],
        )
        connection.commit()

    summary = load_report_summary(str(database_path), export_dir=str(export_dir))

    assert summary.trade_overview is not None
    assert summary.trade_overview.take_profits == 1
    assert summary.trade_overview.stop_losses == 1

    with (export_dir / "trades.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["exit_reason"] == "take_profit_inferred"
    assert rows[1]["exit_reason"] == "stop_loss_inferred"


def test_load_report_summary_uses_richer_tables_and_exports_csv(tmp_path: Path) -> None:
    database_path = tmp_path / "analytics.sqlite3"
    export_dir = tmp_path / "exports"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE trade_analytics (
                position_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional_usd REAL NOT NULL,
                realized_pnl_pct REAL NOT NULL,
                realized_pnl_usd REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                entry_signal_kind TEXT NOT NULL,
                confirmation_signal_kind TEXT NOT NULL,
                regime_score_at_entry INTEGER NOT NULL,
                dom_state_at_entry TEXT NOT NULL,
                entry_rank INTEGER NOT NULL,
                entry_composite_score REAL NOT NULL,
                entry_momentum_z REAL NOT NULL,
                entry_curvature REAL NOT NULL,
                entry_hurst REAL NOT NULL,
                mfe_pct REAL NOT NULL,
                mae_pct REAL NOT NULL,
                post_exit_best_pct REAL NOT NULL,
                post_exit_worst_pct REAL NOT NULL,
                volatility_pct REAL NOT NULL,
                holding_minutes REAL NOT NULL,
                bars_held INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE portfolio_snapshots (
                timestamp TEXT NOT NULL,
                available_balance_usd REAL NOT NULL,
                equity_usd REAL NOT NULL,
                gross_notional_usd REAL NOT NULL,
                net_notional_usd REAL NOT NULL,
                open_positions INTEGER NOT NULL,
                long_positions INTEGER NOT NULL,
                short_positions INTEGER NOT NULL,
                estimated_trade_notional_usd REAL NOT NULL,
                used_risk_usd REAL NOT NULL,
                remaining_risk_usd REAL NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO trade_analytics (
                position_id, ticker, side, opened_at, closed_at, entry_price, exit_price,
                quantity, notional_usd, realized_pnl_pct, realized_pnl_usd, exit_reason,
                entry_signal_kind, confirmation_signal_kind, regime_score_at_entry,
                dom_state_at_entry, entry_rank, entry_composite_score, entry_momentum_z,
                entry_curvature, entry_hurst, mfe_pct, mae_pct, post_exit_best_pct,
                post_exit_worst_pct, volatility_pct, holding_minutes, bars_held
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "AAAUSDT",
                    "Sell",
                    "2026-04-05T00:00:00+00:00",
                    "2026-04-05T01:00:00+00:00",
                    100.0,
                    97.0,
                    10.0,
                    1000.0,
                    0.03,
                    30.0,
                    "take_profit_exit",
                    "entry_ready",
                    "confirmed",
                    2,
                    "neutral",
                    1,
                    1.8,
                    2.1,
                    -0.2,
                    0.7,
                    0.052,
                    -0.014,
                    0.021,
                    -0.006,
                    0.031,
                    60.0,
                    4,
                ),
                (
                    2,
                    "BBBUSDT",
                    "Sell",
                    "2026-04-05T02:00:00+00:00",
                    "2026-04-05T03:00:00+00:00",
                    50.0,
                    52.0,
                    20.0,
                    1000.0,
                    -0.04,
                    -40.0,
                    "stop_loss_exit",
                    "entry_ready",
                    "confirmed_strong",
                    3,
                    "falling",
                    2,
                    2.2,
                    2.5,
                    -0.1,
                    0.8,
                    0.043,
                    -0.031,
                    0.019,
                    -0.027,
                    0.044,
                    60.0,
                    4,
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO portfolio_snapshots (
                timestamp, available_balance_usd, equity_usd, gross_notional_usd,
                net_notional_usd, open_positions, long_positions, short_positions,
                estimated_trade_notional_usd, used_risk_usd, remaining_risk_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2026-04-05T01:00:00+00:00",
                    100000.0,
                    101250.0,
                    50000.0,
                    -50000.0,
                    2,
                    0,
                    2,
                    50000.0,
                    1000.0,
                    9000.0,
                ),
                (
                    "2026-04-05T03:00:00+00:00",
                    98000.0,
                    99000.0,
                    60000.0,
                    -60000.0,
                    1,
                    0,
                    1,
                    50000.0,
                    900.0,
                    9100.0,
                ),
            ],
        )
        connection.commit()

    summary = load_report_summary(
        str(database_path),
        top_n=2,
        export_dir=str(export_dir),
    )

    assert summary.total_rows == 0
    assert summary.trade_overview is not None
    assert summary.trade_overview.trade_count == 2
    assert summary.trade_overview.wins == 1
    assert summary.trade_overview.losses == 1
    assert summary.trade_overview.avg_notional_usd == 1000.0
    assert summary.portfolio_overview is not None
    assert summary.portfolio_overview.snapshot_count == 2
    assert summary.portfolio_overview.max_open_positions == 2
    assert summary.portfolio_overview.avg_estimated_additional_positions == 1.98

    assert (export_dir / "trades.csv").exists()
    assert (export_dir / "trade_daily_summary.csv").exists()
    assert (export_dir / "portfolio_snapshots.csv").exists()
    assert not (export_dir / "signals.csv").exists()

    with (export_dir / "trades.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["ticker"] == "AAAUSDT"
    assert rows[0]["pnl_pct"] == "0.03"
    assert rows[1]["exit_reason"] == "stop_loss_exit"

    with (export_dir / "portfolio_snapshots.csv").open(newline="", encoding="utf-8") as handle:
        portfolio_rows = list(csv.DictReader(handle))
    assert portfolio_rows[0]["estimated_additional_positions"] == "2.0"
    assert portfolio_rows[1]["open_positions"] == "1"

    rendered = format_report(summary)
    assert "Trade analytics:" in rendered
    assert "Portfolio snapshots:" in rendered
    assert "estimated_positions_from_balance" in rendered
