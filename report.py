from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path


SIGNAL_KIND_ORDER = {
    "watchlist": 0,
    "emerging": 1,
    "entry_ready": 2,
    "confirmed": 3,
    "confirmed_strong": 4,
    "none": 5,
}

SIGNAL_KIND_LABELS = {
    "watchlist": "broad intrabar context",
    "emerging": "building intrabar setup",
    "entry_ready": "midpoint intrabar entry candidate",
    "confirmed": "close-confirmed breakout",
    "confirmed_strong": "persistent close-confirmed breakout",
    "none": "no signal",
}


@dataclass(slots=True)
class ReportSummary:
    total_rows: int
    alerted_rows: int
    first_timestamp: str | None
    last_timestamp: str | None
    stage_counts: list[tuple[str, int, int]]
    signal_kind_counts: list[tuple[str, int, int]]
    top_tickers: list[tuple[str, int, int, float, float]]


def load_report_summary(database_path: str, top_n: int = 10) -> ReportSummary:
    with sqlite3.connect(database_path) as connection:
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(signals)")
        }
        total_rows, alerted_rows, first_timestamp, last_timestamp = connection.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COALESCE(SUM(alerted), 0) AS alerted_rows,
                MIN(timestamp) AS first_timestamp,
                MAX(timestamp) AS last_timestamp
            FROM signals
            """
        ).fetchone()
        stage_counts = connection.execute(
            """
            SELECT
                COALESCE(stage, 'confirmed') AS stage,
                COUNT(*) AS total_rows,
                COALESCE(SUM(alerted), 0) AS alerted_rows
            FROM signals
            GROUP BY COALESCE(stage, 'confirmed')
            ORDER BY stage ASC
            """
        ).fetchall()
        if "signal_kind" in existing_columns:
            signal_kind_counts = connection.execute(
                """
                SELECT
                    COALESCE(signal_kind, 'confirmed') AS signal_kind,
                    COUNT(*) AS total_rows,
                    COALESCE(SUM(alerted), 0) AS alerted_rows
                FROM signals
                GROUP BY COALESCE(signal_kind, 'confirmed')
                ORDER BY signal_kind ASC
                """
            ).fetchall()
        else:
            signal_kind_counts = connection.execute(
                """
                SELECT
                    COALESCE(stage, 'confirmed') AS signal_kind,
                    COUNT(*) AS total_rows,
                    COALESCE(SUM(alerted), 0) AS alerted_rows
                FROM signals
                GROUP BY COALESCE(stage, 'confirmed')
                ORDER BY signal_kind ASC
                """
            ).fetchall()
        top_tickers = connection.execute(
            """
            SELECT
                ticker,
                COUNT(*) AS total_rows,
                COALESCE(SUM(alerted), 0) AS alerted_rows,
                AVG(composite_score) AS avg_composite,
                MAX(composite_score) AS max_composite
            FROM signals
            GROUP BY ticker
            ORDER BY alerted_rows DESC, max_composite DESC, ticker ASC
            LIMIT ?
            """,
            (top_n,),
        ).fetchall()

    return ReportSummary(
        total_rows=int(total_rows),
        alerted_rows=int(alerted_rows),
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        stage_counts=[
            (
                str(stage),
                int(stage_rows),
                int(stage_alerts),
            )
            for stage, stage_rows, stage_alerts in stage_counts
        ],
        signal_kind_counts=[
            (
                str(signal_kind),
                int(kind_rows),
                int(kind_alerts),
            )
            for signal_kind, kind_rows, kind_alerts in signal_kind_counts
        ],
        top_tickers=[
            (
                str(ticker),
                int(ticker_rows),
                int(ticker_alerts),
                float(avg_composite),
                float(max_composite),
            )
            for ticker, ticker_rows, ticker_alerts, avg_composite, max_composite in top_tickers
        ],
    )


def format_report(summary: ReportSummary) -> str:
    lines = [
        f"Rows: {summary.total_rows}",
        f"Alerted rows: {summary.alerted_rows}",
        f"First timestamp: {summary.first_timestamp or 'n/a'}",
        f"Last timestamp: {summary.last_timestamp or 'n/a'}",
    ]
    lines.append("Stage rows:")
    if not summary.stage_counts:
        lines.append("  none")
    else:
        for stage, total_rows, alerted_rows in summary.stage_counts:
            lines.append(f"  {stage}: rows={total_rows} alerts={alerted_rows}")
    lines.append("Signal kinds:")
    if not summary.signal_kind_counts:
        lines.append("  none")
    else:
        lines.append("  legend:")
        for signal_kind in ("watchlist", "emerging", "entry_ready", "confirmed", "confirmed_strong"):
            lines.append(f"    {signal_kind}: {SIGNAL_KIND_LABELS[signal_kind]}")
        ordered_signal_kinds = sorted(
            summary.signal_kind_counts,
            key=lambda item: (SIGNAL_KIND_ORDER.get(item[0], 99), item[0]),
        )
        for signal_kind, total_rows, alerted_rows in ordered_signal_kinds:
            lines.append(f"  {signal_kind}: rows={total_rows} alerts={alerted_rows}")
    lines.append("Top tickers:")
    if not summary.top_tickers:
        lines.append("  none")
    else:
        for ticker, total_rows, alerted_rows, avg_composite, max_composite in summary.top_tickers:
            lines.append(
                f"  {ticker}: rows={total_rows} alerts={alerted_rows} "
                f"avg_composite={avg_composite:.3f} max_composite={max_composite:.3f}"
            )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize the signal SQLite log.")
    parser.add_argument("--db", type=str, default="signals.sqlite3", help="Path to the SQLite database.")
    parser.add_argument("--top", type=int, default=10, help="Number of top tickers to show.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_path = str(Path(args.db).expanduser())
    summary = load_report_summary(database_path=database_path, top_n=args.top)
    print(format_report(summary))


if __name__ == "__main__":
    main()
