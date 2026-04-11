from __future__ import annotations

import asyncio
from pathlib import Path

from config import Settings
from exchange import BybitMarketDataClient, HistoricalCandle


def test_market_data_client_serves_cached_ohlc_ranges_without_network(tmp_path: Path) -> None:
    asyncio.run(_exercise_cached_ohlc_range(tmp_path))


async def _exercise_cached_ohlc_range(tmp_path: Path) -> None:
    cache_path = tmp_path / "backtest-cache.sqlite3"
    settings = Settings(
        backtest_cache_enabled=True,
        backtest_cache_path=str(cache_path),
    )
    client = BybitMarketDataClient(session=None, settings=settings)  # type: ignore[arg-type]
    candles = [
        HistoricalCandle(
            start_time_ms=index * 60_000,
            open_price=100.0 + index,
            high_price=101.0 + index,
            low_price=99.0 + index,
            close_price=100.5 + index,
        )
        for index in range(3)
    ]
    client._store_cached_ohlc_range(
        source="bybit",
        category=settings.bybit_category,
        symbol="AAAUSDT",
        interval="1",
        candles=candles,
    )

    loaded = await client.fetch_closed_ohlc_range(
        symbol="AAAUSDT",
        interval="1",
        start_ms=0,
        end_ms=3 * 60_000,
    )

    assert loaded == candles
    assert client.cached_candle_count() == 3
    stats = client.cache_stats_snapshot()
    assert stats.cache_hits == 1
    assert stats.cache_misses == 0
    assert stats.bybit_http_requests == 0
    client.close_cache()
