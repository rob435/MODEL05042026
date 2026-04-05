from __future__ import annotations

from benchmark import BenchmarkResult, build_benchmark_state, format_benchmark
from config import Settings


def test_build_benchmark_state_populates_requested_universe() -> None:
    settings = Settings(universe=["AAAUSDT"], telegram_bot_token=None, telegram_chat_id=None)
    state = build_benchmark_state(settings, ticker_count=5)

    assert len(settings.universe) == 5
    assert "BTCUSDT" in state.price_state
    assert len(state.price_state[settings.universe[0]]) == settings.state_window


def test_format_benchmark_contains_key_metrics() -> None:
    rendered = format_benchmark(BenchmarkResult(samples_ms=[10.0, 12.0, 14.0]), ticker_count=100, cycles=3)

    assert "Tickers: 100" in rendered
    assert "Cycles: 3" in rendered
    assert "Average ms:" in rendered
    assert "P95 ms:" in rendered
