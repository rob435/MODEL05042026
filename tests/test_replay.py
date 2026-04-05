from __future__ import annotations

import pytest

from replay import build_replay_plan


def test_build_replay_plan_rejects_misaligned_histories() -> None:
    history = {
        "AAAUSDT": [(idx, 100.0 + idx) for idx in range(10)],
        "BBBUSDT": [(idx + 1, 200.0 + idx) for idx in range(10)],
    }

    with pytest.raises(ValueError):
        build_replay_plan(
            history_by_symbol=history,
            btc_daily_history=[(idx, 20_000.0 + idx) for idx in range(220)],
            state_window=5,
            replay_cycles=3,
        )


def test_build_replay_plan_returns_tail_window() -> None:
    history = {
        "AAAUSDT": [(idx, 100.0 + idx) for idx in range(12)],
        "BBBUSDT": [(idx, 200.0 + idx) for idx in range(12)],
    }

    plan = build_replay_plan(
        history_by_symbol=history,
        btc_daily_history=[(idx, 20_000.0 + idx) for idx in range(220)],
        state_window=5,
        replay_cycles=3,
    )

    assert plan.replay_timestamps == [9, 10, 11]
    assert len(plan.history_by_symbol["AAAUSDT"]) == 8
