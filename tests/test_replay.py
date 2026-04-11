from __future__ import annotations

import asyncio

import replay as replay_module
from config import Settings
from replay import fetch_replay_plan


def test_fetch_replay_plan_supports_large_windows_with_range_fetch(monkeypatch) -> None:
    asyncio.run(_exercise_large_window_replay_fetch(monkeypatch))


async def _exercise_large_window_replay_fetch(monkeypatch) -> None:
    settings = Settings(
        universe=["AAAUSDT"],
        state_window=4,
        candle_interval="15",
        btc_daily_lookback=5,
    )
    replay_cycles = 1_200
    interval_ms = settings.ticker_interval_ms
    aligned_end_ms = 1_800_000_000_000
    start_ms = aligned_end_ms - ((settings.state_window + replay_cycles) * interval_ms)

    monkeypatch.setattr(replay_module.time, "time", lambda: aligned_end_ms / 1000)

    class StubClient:
        def __init__(self) -> None:
            self.range_calls: list[tuple[str, int, int]] = []

        async def fetch_closed_klines_range(
            self,
            *,
            symbol: str,
            interval: str,
            start_ms: int,
            end_ms: int,
            category=None,
        ):
            self.range_calls.append((symbol, start_ms, end_ms))
            candles = []
            timestamp = start_ms
            price = 100.0
            while timestamp < end_ms:
                candles.append((timestamp, price))
                timestamp += interval_ms
                price += 1.0
            return candles

        async def fetch_closed_klines(self, *, symbol: str, interval: str, limit: int):
            return [(aligned_end_ms - ((limit - index) * 86_400_000), 50.0 + index) for index in range(limit)]

    client = StubClient()
    plan = await fetch_replay_plan(client, settings, replay_cycles, settings.tracked_symbols)

    assert len(plan.replay_timestamps) == replay_cycles
    assert len(plan.history_by_symbol["AAAUSDT"]) == settings.state_window + replay_cycles
    assert client.range_calls == [("AAAUSDT", start_ms, aligned_end_ms), ("BTCUSDT", start_ms, aligned_end_ms)]
