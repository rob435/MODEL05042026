from __future__ import annotations

import pytest

from config import Settings
from state import CandleGapError, MarketState


def test_append_close_raises_on_gap() -> None:
    settings = Settings(universe=["AAAUSDT"])
    state = MarketState(settings=settings)
    state.replace_history(
        "AAAUSDT",
        [
            (0, 100.0),
            (settings.ticker_interval_ms, 101.0),
        ],
    )

    with pytest.raises(CandleGapError):
        state.append_close("AAAUSDT", settings.ticker_interval_ms * 3, 102.0)


def test_provisional_prices_overlay_without_mutating_confirmed_history() -> None:
    settings = Settings(universe=["AAAUSDT"])
    state = MarketState(settings=settings)
    state.replace_history(
        "AAAUSDT",
        [
            (0, 100.0),
            (settings.ticker_interval_ms, 101.0),
        ],
    )

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 105.0) is True

    confirmed = state.get_prices("AAAUSDT")
    provisional = state.get_prices("AAAUSDT", include_provisional=True)

    assert list(confirmed) == [100.0, 101.0]
    assert list(provisional) == [100.0, 101.0, 105.0]

    assert state.append_close("AAAUSDT", settings.ticker_interval_ms * 2, 106.0) is True
    assert state.provisional_state["AAAUSDT"] is None
