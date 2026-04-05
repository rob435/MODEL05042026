from __future__ import annotations

from main import RuntimeStats, format_runtime_summary


def test_format_runtime_summary_contains_all_counters() -> None:
    summary = format_runtime_summary(
        RuntimeStats(
            bootstraps=2,
            macro_refreshes=3,
            processed_cycles=4,
            processed_confirmed_cycles=1,
            processed_emerging_cycles=3,
            websocket_sessions=5,
            websocket_failures=1,
            queue_drops=2,
            confirmed_queue_drops=1,
            emerging_queue_drops=1,
        )
    )

    assert "bootstraps=2" in summary
    assert "macro_refreshes=3" in summary
    assert "processed_cycles=4" in summary
    assert "processed_confirmed_cycles=1" in summary
    assert "processed_emerging_cycles=3" in summary
    assert "websocket_sessions=5" in summary
    assert "websocket_failures=1" in summary
    assert "queue_drops=2" in summary
    assert "confirmed_queue_drops=1" in summary
    assert "emerging_queue_drops=1" in summary
