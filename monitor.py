from __future__ import annotations

import argparse
import sqlite3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect recent runtime health and control events.")
    parser.add_argument("--db", type=str, default="signals.sqlite3", help="SQLite database path.")
    parser.add_argument("--events", type=int, default=10, help="How many runtime events to show.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with sqlite3.connect(args.db) as connection:
        connection.row_factory = sqlite3.Row
        try:
            manifest = connection.execute(
                """
                SELECT *
                FROM run_manifests
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            snapshot = connection.execute(
                """
                SELECT *
                FROM runtime_health_snapshots
                ORDER BY recorded_at DESC
                LIMIT 1
                """
            ).fetchone()
            events = connection.execute(
                """
                SELECT *
                FROM runtime_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (args.events,),
            ).fetchall()
        except sqlite3.OperationalError:
            manifest = None
            snapshot = None
            events = []

    print("Run manifest")
    if manifest is None:
        print("  none")
    else:
        print(
            f"  run_id={manifest['run_id']} started_at={manifest['started_at']} "
            f"git_commit={manifest['git_commit']} config_fingerprint={manifest['config_fingerprint']}"
        )
        print(
            f"  execution_enabled={manifest['execution_enabled']} "
            f"submit_orders={manifest['execution_submit_orders']} demo_mode={manifest['demo_mode']} "
            f"telegram_enabled={manifest['telegram_enabled']} "
            f"operator_pause_new_entries={manifest['operator_pause_new_entries']}"
        )
        print(
            f"  universe_size={manifest['universe_size']} "
            f"momentum_reference_mode={manifest['momentum_reference_mode']} "
            f"cluster_assignment_mode={manifest['cluster_assignment_mode']}"
        )

    print("\nLatest health snapshot")
    if snapshot is None:
        print("  none")
    else:
        print(
            f"  recorded_at={snapshot['recorded_at']} open_positions={snapshot['open_positions']} "
            f"daily_stop_loss_count={snapshot['daily_stop_loss_count']} drift_status={snapshot['drift_status']}"
        )
        print(
            f"  processed_cycles={snapshot['processed_cycles']} confirmed_q={snapshot['confirmed_queue_size']} "
            f"emerging_q={snapshot['emerging_queue_size']} queue_drops={snapshot['queue_drops']}"
        )
        print(
            f"  last_bootstrap_at={snapshot['last_bootstrap_at']} "
            f"last_macro_refresh_at={snapshot['last_macro_refresh_at']} "
            f"last_processed_emerging_at={snapshot['last_processed_emerging_at']} "
            f"last_processed_confirmed_at={snapshot['last_processed_confirmed_at']}"
        )

    print("\nRecent runtime events")
    if not events:
        print("  none")
    else:
        for row in events:
            stage = f" stage={row['stage']}" if row["stage"] else ""
            ticker = f" ticker={row['ticker']}" if row["ticker"] else ""
            print(
                f"  {row['created_at']} {row['severity']} {row['component']} "
                f"{row['event_type']}{stage}{ticker} detail={row['detail']}"
            )


if __name__ == "__main__":
    main()
