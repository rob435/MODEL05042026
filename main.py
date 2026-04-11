from __future__ import annotations

import asyncio
import argparse
import logging
import signal
import ssl
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import certifi
import numpy as np

from alerting import TelegramNotifier
from config import load_settings
from database import SignalDatabase
from exchange import BybitMarketDataClient, BybitTradeClient, MissingCandlesError
from execution import ExecutionEngine, SimulatedExecutionClient
from runtime_monitor import (
    RuntimeTracker,
    build_run_manifest,
    drift_monitor_loop,
    health_snapshot_loop,
)
from runtime_validation import raise_for_invalid_runtime_settings
from signal_engine import SignalEngine
from state import CandleGapError, MarketState

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeStats:
    bootstraps: int = 0
    macro_refreshes: int = 0
    processed_cycles: int = 0
    processed_confirmed_cycles: int = 0
    processed_emerging_cycles: int = 0
    websocket_sessions: int = 0
    websocket_failures: int = 0
    queue_drops: int = 0
    confirmed_queue_drops: int = 0
    emerging_queue_drops: int = 0


def format_runtime_summary(stats: RuntimeStats) -> str:
    return (
        f"bootstraps={stats.bootstraps} "
        f"macro_refreshes={stats.macro_refreshes} "
        f"processed_cycles={stats.processed_cycles} "
        f"processed_confirmed_cycles={stats.processed_confirmed_cycles} "
        f"processed_emerging_cycles={stats.processed_emerging_cycles} "
        f"websocket_sessions={stats.websocket_sessions} "
        f"websocket_failures={stats.websocket_failures} "
        f"queue_drops={stats.queue_drops} "
        f"confirmed_queue_drops={stats.confirmed_queue_drops} "
        f"emerging_queue_drops={stats.emerging_queue_drops}"
    )


async def apply_bootstrap(client: BybitMarketDataClient, state: MarketState, stats: RuntimeStats) -> None:
    LOGGER.info("Bootstrapping %s tracked symbols from Bybit REST", len(state.settings.tracked_symbols))
    payload = await client.bootstrap()
    for symbol, candles in payload.price_history.items():
        state.replace_history(symbol, candles)
    state.global_state.btc_daily_closes = np.asarray(
        [close for _, close in payload.btc_daily_history],
        dtype=float,
    )
    state.replace_btcdom_history(payload.btcdom_history)
    for symbol in state.settings.universe:
        state.reset_intrabar(symbol)
    stats.bootstraps += 1
    LOGGER.info("Bootstrap complete")


async def log_runtime_event(
    database: SignalDatabase,
    *,
    run_id: str | None,
    severity: str,
    component: str,
    event_type: str,
    detail: str,
    stage: str | None = None,
    ticker: str | None = None,
) -> None:
    await database.log_runtime_event(
        run_id=run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        severity=severity,
        component=component,
        event_type=event_type,
        detail=detail,
        stage=stage,
        ticker=ticker,
    )


def enqueue_cycle(
    queue: asyncio.Queue[int],
    cycle_time_ms: int,
    stats: RuntimeStats,
    stage: str,
) -> None:
    try:
        queue.put_nowait(cycle_time_ms)
    except asyncio.QueueFull:
        stats.queue_drops += 1
        if stage == "confirmed":
            stats.confirmed_queue_drops += 1
        else:
            stats.emerging_queue_drops += 1
        LOGGER.warning("%s queue full; dropping cycle at %s", stage.capitalize(), cycle_time_ms)


async def refresh_macro_state_loop(
    client: BybitMarketDataClient,
    state: MarketState,
    stop_event: asyncio.Event,
    stats: RuntimeStats,
    tracker: RuntimeTracker,
    database: SignalDatabase,
) -> None:
    while not stop_event.is_set():
        try:
            daily_candles, btcdom_candles = await asyncio.gather(
                client.fetch_closed_klines(
                    symbol="BTCUSDT",
                    interval="D",
                    limit=state.settings.btc_daily_lookback,
                ),
                client.fetch_btcdom_klines(),
            )
            state.global_state.btc_daily_closes = np.asarray(
                [close for _, close in daily_candles],
                dtype=float,
            )
            state.replace_btcdom_history(btcdom_candles)
            stats.macro_refreshes += 1
            tracker.note_macro_refresh()
            LOGGER.info("Refreshed BTC daily and BTCDOM macro state")
        except Exception:
            LOGGER.exception("Failed refreshing BTC daily and BTCDOM macro state")
            await log_runtime_event(
                database,
                run_id=tracker.run_id,
                severity="error",
                component="macro_refresh",
                event_type="loop_failure",
                detail="Failed refreshing BTC daily and BTCDOM macro state",
            )
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=state.settings.macro_refresh_seconds,
            )
        except asyncio.TimeoutError:
            continue


async def queue_consumer_loop(
    queue: asyncio.Queue[int],
    engine: SignalEngine,
    execution_engine: ExecutionEngine | None,
    stop_event: asyncio.Event,
    process_lock: asyncio.Lock,
    stage: str,
    settle_seconds: float,
    min_cycle_spacing_seconds: float,
    stats: RuntimeStats,
    tracker: RuntimeTracker,
    database: SignalDatabase,
) -> None:
    loop = asyncio.get_running_loop()
    next_eligible_at = 0.0
    while not stop_event.is_set():
        try:
            cycle_time_ms = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        wait_remaining = next_eligible_at - loop.time()
        if wait_remaining > 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_remaining)
            except asyncio.TimeoutError:
                pass
        if settle_seconds > 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settle_seconds)
            except asyncio.TimeoutError:
                pass

        while True:
            try:
                drained_cycle_time_ms = queue.get_nowait()
                cycle_time_ms = max(cycle_time_ms, drained_cycle_time_ms)
            except asyncio.QueueEmpty:
                break
        try:
            async with process_lock:
                ranking_summary = ""
                if stage == "emerging":
                    signals = await engine.process(cycle_time_ms=cycle_time_ms, stage=stage)
                    ranking_summary = engine.format_top_rankings(stage=stage)
                else:
                    signals = []
                execution_actions = []
                if execution_engine is not None:
                    execution_actions = await execution_engine.process_cycle(
                        stage=stage,
                        cycle_time_ms=cycle_time_ms,
                        ranked_signals=signals,
                    )
            stats.processed_cycles += 1
            if stage == "confirmed":
                stats.processed_confirmed_cycles += 1
            else:
                stats.processed_emerging_cycles += 1
            tracker.note_processed_cycle(stage)
            LOGGER.info("Processed %s cycle at %s with %s ranked signals", stage, cycle_time_ms, len(signals))
            if ranking_summary:
                LOGGER.info("Top %s rankings: %s", stage, ranking_summary)
            for action in execution_actions:
                LOGGER.info(
                    "Execution %s %s signal=%s order_id=%s position_id=%s detail=%s",
                    action.action,
                    action.ticker,
                    action.signal_kind,
                    action.order_id,
                    action.position_id,
                    action.detail,
                )
            for ranked_signal in signals:
                if ranked_signal.stage == "emerging" and not ranked_signal.alerted:
                    continue
                LOGGER.info(
                    "%s %s signal %s rank=%s composite=%.2f persistence=%s alerted=%s regime=%s",
                    ranked_signal.stage.upper(),
                    ranked_signal.signal_kind.upper(),
                    ranked_signal.ticker,
                    ranked_signal.rank,
                    ranked_signal.composite_score,
                    ranked_signal.persistence_hits,
                    ranked_signal.alerted,
                    ranked_signal.regime_score,
                )
            next_eligible_at = loop.time() + min_cycle_spacing_seconds
        except Exception:
            LOGGER.exception("%s signal engine processing failed", stage)
            await log_runtime_event(
                database,
                run_id=tracker.run_id,
                severity="error",
                component="queue_consumer",
                event_type="cycle_failure",
                detail=f"{stage} processing failed",
                stage=stage,
            )


async def websocket_supervisor_loop(
    client: BybitMarketDataClient,
    state: MarketState,
    emerging_queue: asyncio.Queue[int],
    confirmed_queue: asyncio.Queue[int],
    stop_event: asyncio.Event,
    stats: RuntimeStats,
    tracker: RuntimeTracker,
    database: SignalDatabase,
) -> None:
    def on_emerging_event(cycle_time_ms: int) -> None:
        tracker.note_websocket_event(confirmed=False)
        enqueue_cycle(emerging_queue, cycle_time_ms, stats, "emerging")

    def on_confirmed_event(cycle_time_ms: int) -> None:
        tracker.note_websocket_event(confirmed=True)
        enqueue_cycle(confirmed_queue, cycle_time_ms, stats, "confirmed")

    delay = state.settings.reconnect_base_delay
    while not stop_event.is_set():
        try:
            LOGGER.info("Starting WebSocket supervisor connection")
            stats.websocket_sessions += 1
            await client.stream_candles(
                symbols=state.settings.tracked_symbols,
                on_provisional_candle=state.update_provisional,
                on_closed_candle=state.append_close,
                on_emerging_event=on_emerging_event,
                on_confirmed_event=on_confirmed_event,
            )
            delay = state.settings.reconnect_base_delay
        except asyncio.CancelledError:
            raise
        except Exception:
            stats.websocket_failures += 1
            LOGGER.exception("WebSocket stream failed; reloading state and reconnecting")
            await log_runtime_event(
                database,
                run_id=tracker.run_id,
                severity="error",
                component="websocket",
                event_type="stream_failure",
                detail="WebSocket stream failed; attempting bootstrap recovery",
            )
            try:
                await apply_bootstrap(client, state, stats)
                tracker.note_bootstrap()
                for symbol in state.settings.universe:
                    enqueue_cycle(
                        confirmed_queue,
                        state.close_times_ms[symbol][-1],
                        stats,
                        "confirmed",
                    )
            except (MissingCandlesError, CandleGapError):
                LOGGER.exception("Bootstrap recovery failed after WebSocket disconnect")
            except Exception:
                LOGGER.exception("Unexpected recovery failure after WebSocket disconnect")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                delay = min(delay * 2, state.settings.reconnect_max_delay)
                continue


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_client_session() -> aiohttp.ClientSession:
    timeout = aiohttp.ClientTimeout(total=30)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    return aiohttp.ClientSession(timeout=timeout, connector=connector)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MODEL050426 service.")
    parser.add_argument(
        "--run-seconds",
        type=float,
        default=None,
        help="Optional bounded runtime for soak/smoke validation.",
    )
    parser.add_argument(
        "--disable-telegram",
        action="store_true",
        help="Disable Telegram sends for this run.",
    )
    return parser.parse_args()


async def run(run_seconds: float | None = None, disable_telegram: bool = False) -> RuntimeStats:
    settings = load_settings()
    configure_logging(settings.log_level)
    validation_messages = raise_for_invalid_runtime_settings(
        settings,
        disable_telegram=disable_telegram,
    )
    LOGGER.info("Starting MODEL050426 with %s universe symbols", len(settings.universe))

    emerging_queue: asyncio.Queue[int] = asyncio.Queue(maxsize=settings.queue_maxsize)
    confirmed_queue: asyncio.Queue[int] = asyncio.Queue(maxsize=settings.queue_maxsize)
    stop_event = asyncio.Event()
    stats = RuntimeStats()
    process_lock = asyncio.Lock()
    manifest = build_run_manifest(
        settings,
        disable_telegram=disable_telegram,
        cwd=str(Path(__file__).resolve().parent),
    )
    tracker = RuntimeTracker(run_id=manifest.run_id)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)
    if run_seconds is not None and run_seconds > 0:
        loop.call_later(run_seconds, stop_event.set)

    async with build_client_session() as session:
        database = SignalDatabase(settings.sqlite_path)
        await database.initialize()
        await database.log_run_manifest(**asdict(manifest))
        await log_runtime_event(
            database,
            run_id=manifest.run_id,
            severity="info",
            component="runtime",
            event_type="run_started",
            detail=(
                f"git_commit={manifest.git_commit} "
                f"config_fingerprint={manifest.config_fingerprint}"
            ),
        )

        state = MarketState(settings=settings)
        client = BybitMarketDataClient(session=session, settings=settings)
        notifier = TelegramNotifier(
            session=session,
            bot_token=None if disable_telegram else settings.telegram_bot_token,
            chat_id=None if disable_telegram else settings.telegram_chat_id,
        )
        engine = SignalEngine(
            settings=settings,
            state=state,
            database=database,
            notifier=notifier,
        )
        execution_client = (
            BybitTradeClient(session=session, settings=settings)
            if settings.execution_submit_orders
            else SimulatedExecutionClient(settings)
        )
        execution_engine = ExecutionEngine(
            settings=settings,
            state=state,
            database=database,
            notifier=notifier,
            client=execution_client,
        )
        execution_engine.set_runtime_run_id(manifest.run_id)

        for message in validation_messages:
            log_fn = LOGGER.warning if message.level == "warning" else LOGGER.info
            log_fn("Runtime config %s: %s", message.code, message.message)
            await log_runtime_event(
                database,
                run_id=manifest.run_id,
                severity=message.level,
                component="runtime_validation",
                event_type=message.code,
                detail=message.message,
            )

        await apply_bootstrap(client, state, stats)
        tracker.note_bootstrap()
        for symbol in settings.universe:
            enqueue_cycle(
                confirmed_queue,
                state.close_times_ms[symbol][-1],
                stats,
                "confirmed",
            )

        confirmed_consumer_task = asyncio.create_task(
            queue_consumer_loop(
                confirmed_queue,
                engine,
                execution_engine,
                stop_event,
                process_lock,
                "confirmed",
                settings.cycle_settle_seconds,
                0.0,
                stats,
                tracker,
                database,
            )
        )
        emerging_consumer_task = asyncio.create_task(
            queue_consumer_loop(
                emerging_queue,
                engine,
                execution_engine,
                stop_event,
                process_lock,
                "emerging",
                settings.emerging_settle_seconds,
                settings.emerging_interval_seconds,
                stats,
                tracker,
                database,
            )
        )
        websocket_task = asyncio.create_task(
            websocket_supervisor_loop(
                client,
                state,
                emerging_queue,
                confirmed_queue,
                stop_event,
                stats,
                tracker,
                database,
            )
        )
        macro_task = asyncio.create_task(
            refresh_macro_state_loop(client, state, stop_event, stats, tracker, database)
        )
        health_task = (
            asyncio.create_task(
                health_snapshot_loop(
                    database=database,
                    tracker=tracker,
                    stats=stats,
                    confirmed_queue=confirmed_queue,
                    emerging_queue=emerging_queue,
                    stop_event=stop_event,
                    settings=settings,
                )
            )
            if settings.runtime_health_snapshot_enabled
            else None
        )
        drift_task = (
            asyncio.create_task(
                drift_monitor_loop(
                    database=database,
                    execution_engine=execution_engine,
                    tracker=tracker,
                    stop_event=stop_event,
                    settings=settings,
                )
            )
            if settings.runtime_drift_check_enabled
            else None
        )

        await stop_event.wait()

        tasks = [
            confirmed_consumer_task,
            emerging_consumer_task,
            websocket_task,
            macro_task,
            health_task,
            drift_task,
        ]
        for task in tasks:
            if task is None:
                continue
            task.cancel()
        for task in tasks:
            if task is None:
                continue
            with suppress(asyncio.CancelledError):
                await task
        await log_runtime_event(
            database,
            run_id=manifest.run_id,
            severity="info",
            component="runtime",
            event_type="run_stopped",
            detail=format_runtime_summary(stats),
        )
        database.close()
    LOGGER.info("Runtime summary: %s", format_runtime_summary(stats))
    return stats


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(run_seconds=args.run_seconds, disable_telegram=args.disable_telegram))
