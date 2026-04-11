from __future__ import annotations

import asyncio

from config import Settings
from database import SignalDatabase
from main import RuntimeStats
from runtime_monitor import RuntimeTracker, build_config_fingerprint, build_health_snapshot


def test_build_config_fingerprint_changes_when_live_controls_change() -> None:
    first = build_config_fingerprint(Settings())
    second = build_config_fingerprint(Settings(max_open_positions=5))

    assert first != second


def test_build_health_snapshot_uses_db_and_tracker(tmp_path) -> None:
    async def _exercise() -> None:
        database = SignalDatabase(str(tmp_path / "runtime-health.sqlite3"))
        await database.initialize()
        tracker = RuntimeTracker(run_id="run-123")
        tracker.note_bootstrap()
        tracker.note_macro_refresh()
        tracker.note_websocket_event(confirmed=False)
        tracker.note_processed_cycle("emerging")
        tracker.note_drift_check("ok")
        snapshot = await build_health_snapshot(
            database=database,
            tracker=tracker,
            stats=RuntimeStats(processed_emerging_cycles=2),
            confirmed_queue=asyncio.Queue(),
            emerging_queue=asyncio.Queue(),
            operator_pause_new_entries=False,
        )
        assert snapshot.run_id == "run-123"
        assert snapshot.bootstraps == 0
        assert snapshot.processed_emerging_cycles == 2
        assert snapshot.drift_status == "ok"
        assert snapshot.last_bootstrap_at is not None
        database.close()

    asyncio.run(_exercise())
