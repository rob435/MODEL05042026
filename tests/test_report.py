from __future__ import annotations

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
