from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import numpy as np

from alerting import TelegramNotifier
from config import Settings, load_settings
from database import SignalDatabase
from exchange import BybitMarketDataClient, MissingCandlesError
from signal_engine import SignalEngine
from state import MarketState


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ReplayPlan:
    replay_timestamps: list[int]
    history_by_symbol: dict[str, list[tuple[int, float]]]
    btc_daily_history: list[tuple[int, float]]


def build_replay_plan(
    history_by_symbol: dict[str, list[tuple[int, float]]],
    btc_daily_history: list[tuple[int, float]],
    state_window: int,
    replay_cycles: int,
) -> ReplayPlan:
    if replay_cycles <= 0:
        raise ValueError("replay_cycles must be positive")
    if not history_by_symbol:
        raise ValueError("history_by_symbol cannot be empty")

    required = state_window + replay_cycles
    reference_symbol, reference_candles = next(iter(history_by_symbol.items()))
    reference_timestamps = [timestamp for timestamp, _ in reference_candles[-required:]]
    if len(reference_timestamps) < required:
        raise ValueError(f"{reference_symbol} only has {len(reference_timestamps)} candles, need {required}")

    for symbol, candles in history_by_symbol.items():
        tail = candles[-required:]
        timestamps = [timestamp for timestamp, _ in tail]
        if len(timestamps) < required:
            raise ValueError(f"{symbol} only has {len(timestamps)} candles, need {required}")
        if timestamps != reference_timestamps:
            raise ValueError(f"{symbol} candle timestamps do not align with {reference_symbol}")

    return ReplayPlan(
        replay_timestamps=reference_timestamps[-replay_cycles:],
        history_by_symbol={symbol: candles[-required:] for symbol, candles in history_by_symbol.items()},
        btc_daily_history=btc_daily_history,
    )


async def fetch_replay_plan(
    client: BybitMarketDataClient,
    settings: Settings,
    replay_cycles: int,
    symbols: list[str],
) -> ReplayPlan:
    required = settings.state_window + replay_cycles
    if required > 950:
        raise ValueError("Replay currently supports at most 950 total 15m candles per symbol")

    async def _load_symbol(symbol: str) -> tuple[str, list[tuple[int, float]]]:
        candles = await client.fetch_closed_klines(
            symbol=symbol,
            interval=settings.candle_interval,
            limit=required,
        )
        return symbol, candles

    history_by_symbol = dict(await asyncio.gather(*(_load_symbol(symbol) for symbol in symbols)))
    btc_daily_history = await client.fetch_closed_klines(
        symbol="BTCUSDT",
        interval="D",
        limit=settings.btc_daily_lookback,
    )
    return build_replay_plan(
        history_by_symbol=history_by_symbol,
        btc_daily_history=btc_daily_history,
        state_window=settings.state_window,
        replay_cycles=replay_cycles,
    )


async def run_replay(settings: Settings, replay_cycles: int, sqlite_path: str, enable_telegram: bool) -> None:
    async with aiohttp.ClientSession() as session:
        client = BybitMarketDataClient(session=session, settings=settings)
        plan = await fetch_replay_plan(client, settings, replay_cycles, settings.tracked_symbols)

        state = MarketState(settings=settings)
        for symbol, candles in plan.history_by_symbol.items():
            state.replace_history(symbol, candles[: settings.state_window])
        state.global_state.btc_daily_closes = np.asarray(
            [close for _, close in plan.btc_daily_history],
            dtype=float,
        )

        database = SignalDatabase(sqlite_path)
        await database.initialize()
        notifier = TelegramNotifier(
            session=session,
            bot_token=settings.telegram_bot_token if enable_telegram else None,
            chat_id=settings.telegram_chat_id if enable_telegram else None,
        )
        engine = SignalEngine(
            settings=settings,
            state=state,
            database=database,
            notifier=notifier,
        )

        for offset, cycle_time_ms in enumerate(plan.replay_timestamps):
            history_index = settings.state_window + offset
            for symbol, candles in plan.history_by_symbol.items():
                candle_time_ms, close_price = candles[history_index]
                if candle_time_ms != cycle_time_ms:
                    raise MissingCandlesError(f"{symbol} replay candle misaligned at {history_index}")
                appended = state.append_close(symbol, candle_time_ms, close_price)
                if not appended:
                    raise RuntimeError(f"Replay failed to append {symbol} candle {candle_time_ms}")
            await engine.process(cycle_time_ms=cycle_time_ms)

        database.close()
        LOGGER.info("Replay complete for %s cycles. SQLite log: %s", replay_cycles, sqlite_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay recent Bybit candles through the production signal engine.")
    parser.add_argument("--cycles", type=int, default=96, help="Number of 15m cycles to replay after warmup.")
    parser.add_argument("--db", type=str, default="replay-signals.sqlite3", help="SQLite output path for replay logs.")
    parser.add_argument(
        "--enable-telegram",
        action="store_true",
        help="Allow Telegram sends during replay. Off by default.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    asyncio.run(
        run_replay(
            settings=settings,
            replay_cycles=args.cycles,
            sqlite_path=str(Path(args.db).expanduser()),
            enable_telegram=args.enable_telegram,
        )
    )


if __name__ == "__main__":
    main()
