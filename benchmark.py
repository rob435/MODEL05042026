from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from dataclasses import dataclass

import numpy as np

from config import Settings
from database import SignalDatabase
from signal_engine import SignalEngine
from state import MarketState


@dataclass(slots=True)
class BenchmarkResult:
    samples_ms: list[float]

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.samples_ms)

    @property
    def max_ms(self) -> float:
        return max(self.samples_ms)

    @property
    def p95_ms(self) -> float:
        if len(self.samples_ms) == 1:
            return self.samples_ms[0]
        return float(np.percentile(np.asarray(self.samples_ms, dtype=float), 95))


class NullNotifier:
    async def send(self, payload) -> bool:
        return False


def build_benchmark_state(settings: Settings, ticker_count: int) -> MarketState:
    settings.universe = [f"T{idx:03d}USDT" for idx in range(ticker_count)]
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    base_curve = np.linspace(100.0, 140.0, settings.state_window)
    rng = np.random.default_rng(7)

    state.replace_history(
        "BTCUSDT",
        list(zip(timestamps, (20_000 + np.linspace(0, 5_000, settings.state_window)).tolist())),
    )
    for idx, symbol in enumerate(settings.universe):
        prices = base_curve + idx * 0.05 + np.cumsum(
            np.abs(rng.normal(0.01 + idx * 1e-4, 0.02, settings.state_window))
        )
        state.replace_history(symbol, list(zip(timestamps, prices.tolist())))
    state.global_state.btc_daily_closes = np.asarray(
        [20_000 + idx * 50 for idx in range(settings.btc_daily_lookback)],
        dtype=float,
    )
    return state


async def run_benchmark(ticker_count: int, cycles: int) -> BenchmarkResult:
    database_path = "/tmp/outlier-benchmark.sqlite3"
    settings = Settings(
        sqlite_path=database_path,
        universe=[f"T{idx:03d}USDT" for idx in range(ticker_count)],
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    state = build_benchmark_state(settings, ticker_count=ticker_count)
    database = SignalDatabase(database_path)
    await database.initialize()
    engine = SignalEngine(settings=settings, state=state, database=database, notifier=NullNotifier())
    cycle_time_ms = state.close_times_ms["BTCUSDT"][-1]

    samples_ms: list[float] = []
    for _ in range(cycles):
        started = time.perf_counter()
        await engine.process(cycle_time_ms=cycle_time_ms)
        samples_ms.append((time.perf_counter() - started) * 1000.0)

    database.close()
    if os.path.exists(database_path):
        os.remove(database_path)
    return BenchmarkResult(samples_ms=samples_ms)


def format_benchmark(result: BenchmarkResult, ticker_count: int, cycles: int) -> str:
    return (
        f"Tickers: {ticker_count}\n"
        f"Cycles: {cycles}\n"
        f"Average ms: {result.avg_ms:.2f}\n"
        f"P95 ms: {result.p95_ms:.2f}\n"
        f"Max ms: {result.max_ms:.2f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the signal engine with synthetic data.")
    parser.add_argument("--tickers", type=int, default=100, help="Number of synthetic tickers to benchmark.")
    parser.add_argument("--cycles", type=int, default=20, help="Number of benchmark cycles to execute.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_benchmark(ticker_count=args.tickers, cycles=args.cycles))
    print(format_benchmark(result, ticker_count=args.tickers, cycles=args.cycles))


if __name__ == "__main__":
    main()
