from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import logging
import math
import os
import pickle
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import numpy as np

from config import Settings, load_settings
from database import SignalDatabase
from execution import ExecutionEngine
from exchange import (
    BybitMarketDataClient,
    HistoricalCandle,
    MissingCandlesError,
    interval_to_milliseconds,
)
from indicators import log_returns
from replay import ReplayPlan, build_replay_plan, fetch_replay_plan
from report import ReportSummary, format_report, load_report_summary
from signal_engine import RankedSignal, SignalEngine
from state import MarketState
from universe import ticker_cluster


LOGGER = logging.getLogger(__name__)


class NullNotifier:
    enabled = False

    async def send(self, payload) -> bool:
        return False

    async def send_execution(self, payload) -> bool:
        return False


@dataclass(slots=True)
class BacktestResult:
    database_path: str
    summary: ReportSummary


@dataclass(slots=True)
class MinuteReplayPlan:
    confirmed_plan: ReplayPlan
    intrabar_by_symbol: dict[str, dict[int, list[HistoricalCandle]]]
    btc_daily_history: list[tuple[int, float]]
    btcdom_history: list[tuple[int, float]]
    active_universe: list[str] | None = None


@dataclass(slots=True)
class SimulatedPosition:
    ticker: str
    quantity: float
    entry_price: float
    raw_entry_price: float
    notional_usd: float
    opened_at_ms: int
    entry_stage: str
    entry_signal_kind: str
    cluster_label: str
    entry_diagnostics: str
    take_profit_price: float
    stop_loss_price: float
    entry_fee_usd: float
    entry_slippage_usd: float
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    peak_favorable_price: float | None = None
    peak_adverse_price: float | None = None


@dataclass(slots=True)
class BacktestTrade:
    ticker: str
    side: str
    opened_at: str
    closed_at: str
    entry_stage: str
    entry_signal_kind: str
    cluster_label: str
    entry_diagnostics: str
    exit_reason: str
    quantity: float
    entry_price: float
    exit_price: float
    notional_usd: float
    gross_pnl_usd: float
    net_pnl_usd: float
    pnl_pct: float
    entry_fee_usd: float
    exit_fee_usd: float
    entry_slippage_usd: float
    exit_slippage_usd: float
    holding_minutes: float
    mfe_pct: float
    mae_pct: float
    post_exit_best_pct: float | None = None
    post_exit_worst_pct: float | None = None
    volatility_pct: float | None = None


@dataclass(slots=True)
class PostExitTracker:
    trade_index: int
    ticker: str
    exit_price: float
    bars_remaining: int
    best_pct: float = 0.0
    worst_pct: float = 0.0
    close_prices: list[float] | None = None

    def __post_init__(self) -> None:
        if self.close_prices is None:
            self.close_prices = [self.exit_price]


@dataclass(slots=True)
class EquitySnapshot:
    timestamp: str
    equity_usd: float
    gross_exposure_usd: float
    realized_gross_pnl_usd: float
    realized_net_pnl_usd: float
    unrealized_pnl_usd: float
    fees_usd: float
    open_positions: int
    drawdown_pct: float


@dataclass(slots=True)
class DailyBacktestSummary:
    day: str
    trades: int
    wins: int
    losses: int
    gross_pnl_usd: float
    net_pnl_usd: float
    fees_usd: float
    return_pct: float


@dataclass(slots=True)
class TickerBacktestSummary:
    ticker: str
    trades: int
    wins: int
    losses: int
    gross_pnl_usd: float
    net_pnl_usd: float
    avg_pnl_pct: float


@dataclass(slots=True)
class ComprehensiveBacktestSummary:
    mode: str
    starting_equity_usd: float
    ending_equity_usd: float
    total_return_pct: float
    gross_pnl_usd: float
    net_pnl_usd: float
    fees_usd: float
    slippage_usd: float
    max_drawdown_pct: float
    trade_count: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float | None
    avg_win_usd: float | None
    avg_loss_usd: float | None
    expectancy_usd: float | None
    avg_holding_minutes: float | None
    avg_mfe_pct: float | None
    avg_mae_pct: float | None
    avg_post_exit_best_pct: float | None
    avg_post_exit_worst_pct: float | None
    avg_volatility_pct: float | None
    take_profits: int
    stop_losses: int
    forced_exits: int
    entry_ready_signals: int
    entries_filled: int
    skipped_duplicate_ticker: int
    skipped_max_open_positions: int
    skipped_cluster_limit: int
    skipped_max_entries_per_rebalance: int
    skipped_daily_stop_losses: int
    skipped_gross_exposure_cap: int
    skipped_too_small: int
    max_open_positions_observed: int
    signal_summary: ReportSummary
    daily: list[DailyBacktestSummary]
    tickers: list[TickerBacktestSummary]


@dataclass(slots=True)
class ComprehensiveBacktestResult:
    database_path: str
    summary: ComprehensiveBacktestSummary
    trades: list[BacktestTrade]
    equity_curve: list[EquitySnapshot]


@dataclass(slots=True)
class SweepWindowComparison:
    window_end: str
    filter_on_trades: int
    filter_off_trades: int
    filter_on_net_pnl_usd: float
    filter_off_net_pnl_usd: float
    filter_on_return_pct: float
    filter_off_return_pct: float
    filter_on_max_drawdown_pct: float
    filter_off_max_drawdown_pct: float
    filter_on_entry_ready_signals: int
    filter_off_entry_ready_signals: int
    winner: str


@dataclass(slots=True)
class SweepComparisonSummary:
    windows_requested: int
    windows_completed: int
    windows_skipped: int
    filter_on_better: int
    filter_off_better: int
    ties: int
    avg_filter_on_net_pnl_usd: float | None
    avg_filter_off_net_pnl_usd: float | None
    avg_filter_on_return_pct: float | None
    avg_filter_off_return_pct: float | None
    avg_filter_on_drawdown_pct: float | None
    avg_filter_off_drawdown_pct: float | None


@dataclass(slots=True)
class SweepComparisonResult:
    summary: SweepComparisonSummary
    windows: list[SweepWindowComparison]
    skipped_windows: list[str]


@dataclass(slots=True)
class PrefetchSummary:
    cache_path: str
    lookback_days: int
    start_utc: str
    end_utc: str
    tracked_symbols: int
    bybit_symbol_intervals: int
    bybit_candles: int
    btc_daily_candles: int
    btcdom_candles: int
    cache_rows: int


@dataclass(slots=True)
class BacktestVariantSpec:
    name: str
    overrides: dict[str, bool | int | float | str]


@dataclass(slots=True)
class BacktestVariantSummary:
    name: str
    database_path: str
    trade_count: int
    wins: int
    losses: int
    net_pnl_usd: float
    total_return_pct: float
    max_drawdown_pct: float
    profit_factor: float | None
    entry_ready_signals: int
    entries_filled: int


@dataclass(slots=True)
class BacktestVariantRunResult:
    variants: list[BacktestVariantSummary]


@dataclass(slots=True)
class WalkForwardWindowResult:
    train_window_end: str
    test_window_end: str
    selected_variant: str
    selected_variant_rank: int
    train_net_pnl_usd: float
    train_return_pct: float
    test_net_pnl_usd: float
    test_return_pct: float
    test_max_drawdown_pct: float
    test_trade_count: int


@dataclass(slots=True)
class WalkForwardCandidateResult:
    test_window_end: str
    variant: str
    rank: int
    selected: bool
    train_net_pnl_usd: float
    train_return_pct: float
    train_max_drawdown_pct: float
    train_trade_count: int


@dataclass(slots=True)
class WalkForwardSummary:
    windows_requested: int
    windows_completed: int
    windows_skipped: int
    profitable_test_windows: int
    losing_test_windows: int
    total_test_net_pnl_usd: float | None
    avg_test_net_pnl_usd: float | None
    avg_test_return_pct: float | None
    avg_test_drawdown_pct: float | None


@dataclass(slots=True)
class WalkForwardResult:
    summary: WalkForwardSummary
    windows: list[WalkForwardWindowResult]
    candidates: list[WalkForwardCandidateResult]
    skipped_windows: list[str]


def _build_backtest_settings(
    settings: Settings,
    *,
    sqlite_path: str,
    intraday_regime_filter_enabled: bool | None = None,
) -> Settings:
    return replace(
        settings,
        sqlite_path=sqlite_path,
        execution_enabled=True,
        execution_submit_orders=False,
        demo_mode=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
        telegram_signal_alerts_enabled=False,
        watchlist_telegram_enabled=False,
        analytics_post_exit_bars=0,
        intraday_regime_filter_enabled=(
            settings.intraday_regime_filter_enabled
            if intraday_regime_filter_enabled is None
            else intraday_regime_filter_enabled
        ),
    )


def _build_comprehensive_settings(
    settings: Settings,
    *,
    sqlite_path: str,
    intraday_regime_filter_enabled: bool | None = None,
) -> Settings:
    return replace(
        settings,
        sqlite_path=sqlite_path,
        backtest_mode=True,
        execution_enabled=False,
        execution_submit_orders=False,
        demo_mode=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
        telegram_signal_alerts_enabled=False,
        watchlist_telegram_enabled=False,
        analytics_enabled=False,
        intraday_regime_filter_enabled=(
            settings.intraday_regime_filter_enabled
            if intraday_regime_filter_enabled is None
            else intraday_regime_filter_enabled
        ),
    )


def _empty_report_summary() -> ReportSummary:
    return ReportSummary(
        total_rows=0,
        alerted_rows=0,
        first_timestamp=None,
        last_timestamp=None,
        stage_counts=[],
        signal_kind_counts=[],
        top_tickers=[],
        trade_overview=None,
        trade_daily=[],
        portfolio_overview=None,
    )


async def run_backtest_plan(
    settings: Settings,
    plan: ReplayPlan,
    *,
    sqlite_path: str,
    intraday_regime_filter_enabled: bool | None = None,
) -> BacktestResult:
    backtest_settings = _build_backtest_settings(
        settings,
        sqlite_path=sqlite_path,
        intraday_regime_filter_enabled=intraday_regime_filter_enabled,
    )
    database_file = Path(backtest_settings.sqlite_path)
    if database_file.exists():
        database_file.unlink()

    state = MarketState(settings=backtest_settings)
    for symbol, candles in plan.history_by_symbol.items():
        state.replace_history(symbol, candles[: backtest_settings.state_window])
    state.global_state.btc_daily_closes = np.asarray(
        [close for _, close in plan.btc_daily_history],
        dtype=float,
    )

    database = SignalDatabase(backtest_settings.sqlite_path)
    await database.initialize()
    notifier = NullNotifier()
    signal_engine = SignalEngine(
        settings=backtest_settings,
        state=state,
        database=database,
        notifier=notifier,
    )
    execution_engine = ExecutionEngine(
        settings=backtest_settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    try:
        for offset, cycle_time_ms in enumerate(plan.replay_timestamps):
            history_index = backtest_settings.state_window + offset
            for symbol, candles in plan.history_by_symbol.items():
                candle_time_ms, close_price = candles[history_index]
                if candle_time_ms != cycle_time_ms:
                    raise MissingCandlesError(
                        f"{symbol} replay candle misaligned at {history_index}"
                    )
                provisional_updated = state.update_provisional(symbol, candle_time_ms, close_price)
                if not provisional_updated:
                    raise RuntimeError(
                        f"Backtest failed to stage provisional candle for {symbol} at {candle_time_ms}"
                    )
            emerging_signals = await signal_engine.process(
                cycle_time_ms=cycle_time_ms,
                stage="emerging",
            )
            await execution_engine.process_cycle(
                stage="emerging",
                cycle_time_ms=cycle_time_ms,
                ranked_signals=emerging_signals,
            )

            for symbol, candles in plan.history_by_symbol.items():
                candle_time_ms, close_price = candles[history_index]
                appended = state.append_close(symbol, candle_time_ms, close_price)
                if not appended:
                    raise RuntimeError(
                        f"Backtest failed to append confirmed candle for {symbol} at {candle_time_ms}"
                    )
            await execution_engine.process_cycle(
                stage="confirmed",
                cycle_time_ms=cycle_time_ms,
                ranked_signals=[],
            )
    finally:
        database.close()

    summary = (
        _empty_report_summary()
        if backtest_settings.backtest_research_fast
        else load_report_summary(backtest_settings.sqlite_path, top_n=10)
    )
    return BacktestResult(database_path=backtest_settings.sqlite_path, summary=summary)


def _minute_close_timestamp(candle: HistoricalCandle, interval_ms: int) -> int:
    return candle.start_time_ms + interval_ms


def _align_down(timestamp_ms: int, interval_ms: int) -> int:
    return (timestamp_ms // interval_ms) * interval_ms


def _window_end_ms_to_label(window_end_ms: int) -> str:
    return datetime.fromtimestamp(window_end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _resolve_end_ms(settings: Settings, end_date: str | None = None) -> int:
    anchor_dt = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        if end_date
        else datetime.now(timezone.utc)
    )
    return _align_down(int(anchor_dt.timestamp() * 1000), settings.ticker_interval_ms)


def _coerce_setting_value(settings: Settings, key: str, raw_value: str) -> bool | int | float | str:
    if not hasattr(settings, key):
        raise ValueError(f"Unknown setting: {key}")
    current_value = getattr(settings, key)
    if isinstance(current_value, bool):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value for {key}: {raw_value}")
    if isinstance(current_value, int) and not isinstance(current_value, bool):
        return int(raw_value)
    if isinstance(current_value, float):
        return float(raw_value)
    if isinstance(current_value, str):
        return raw_value
    raise ValueError(f"Unsupported grid setting type for {key}: {type(current_value).__name__}")


def _parse_grid_setting(settings: Settings, raw: str) -> tuple[str, list[bool | int | float | str]]:
    if "=" not in raw:
        raise ValueError(f"Grid setting must be KEY=value1,value2,...: {raw}")
    key, raw_values = raw.split("=", 1)
    key = key.strip()
    values = [token.strip() for token in raw_values.split(",") if token.strip()]
    if not key or not values:
        raise ValueError(f"Grid setting must be KEY=value1,value2,...: {raw}")
    return key, [_coerce_setting_value(settings, key, value) for value in values]


def _build_variant_specs(
    settings: Settings,
    grid_settings: list[str],
) -> list[BacktestVariantSpec]:
    if not grid_settings:
        return []
    parsed = [_parse_grid_setting(settings, raw) for raw in grid_settings]
    keys = [key for key, _ in parsed]
    values_list = [values for _, values in parsed]
    variants: list[BacktestVariantSpec] = []
    for combo in itertools.product(*values_list):
        overrides = {key: value for key, value in zip(keys, combo)}
        label = ",".join(f"{key}={value}" for key, value in overrides.items())
        variants.append(BacktestVariantSpec(name=label, overrides=overrides))
    return variants


def _build_stress_variant_specs(
    settings: Settings,
    names: list[str],
) -> list[BacktestVariantSpec]:
    specs: list[BacktestVariantSpec] = []
    for raw_name in names:
        name = raw_name.strip().lower()
        if name == "costly":
            overrides = {
                "backtest_fee_rate": settings.backtest_fee_rate * 1.5,
                "backtest_slippage_bps": settings.backtest_slippage_bps * 2.0,
            }
        elif name == "liquidity_crunch":
            overrides = {
                "backtest_fee_rate": settings.backtest_fee_rate * 1.75,
                "backtest_slippage_bps": settings.backtest_slippage_bps * 3.0,
                "backtest_max_gross_exposure_multiple": min(
                    settings.backtest_max_gross_exposure_multiple,
                    1.0,
                ),
            }
        elif name == "hostile":
            overrides = {
                "backtest_fee_rate": settings.backtest_fee_rate * 2.0,
                "backtest_slippage_bps": settings.backtest_slippage_bps * 4.0,
                "backtest_max_gross_exposure_multiple": min(
                    settings.backtest_max_gross_exposure_multiple,
                    0.8,
                ),
                "max_positions_per_cluster": max(1, min(settings.max_positions_per_cluster, 1)),
            }
        else:
            raise ValueError(f"Unknown stress profile: {raw_name}")
        specs.append(BacktestVariantSpec(name=f"stress={name}", overrides=overrides))
    return specs


def _combine_variant_specs(
    grid_variants: list[BacktestVariantSpec],
    stress_variants: list[BacktestVariantSpec],
) -> list[BacktestVariantSpec]:
    if not grid_variants and not stress_variants:
        return []
    if not stress_variants:
        return grid_variants
    if not grid_variants:
        return stress_variants
    combined: list[BacktestVariantSpec] = []
    for grid_variant in grid_variants:
        for stress_variant in stress_variants:
            overrides = dict(grid_variant.overrides)
            overrides.update(stress_variant.overrides)
            combined.append(
                BacktestVariantSpec(
                    name=f"{grid_variant.name},{stress_variant.name}",
                    overrides=overrides,
                )
            )
    return combined


def _group_intrabar_candles(
    candles: list[HistoricalCandle],
    *,
    parent_interval_ms: int,
) -> dict[int, list[HistoricalCandle]]:
    grouped: dict[int, list[HistoricalCandle]] = defaultdict(list)
    for candle in candles:
        bucket_start_ms = (candle.start_time_ms // parent_interval_ms) * parent_interval_ms
        grouped[bucket_start_ms].append(candle)
    for bucket in grouped.values():
        bucket.sort(key=lambda item: item.start_time_ms)
    return dict(grouped)


def _has_complete_aligned_history(
    candles: list[tuple[int, float]],
    *,
    start_ms: int,
    end_ms: int,
    interval_ms: int,
) -> bool:
    expected = (end_ms - start_ms) // interval_ms
    if len(candles) != expected:
        return False
    return all(timestamp == start_ms + (idx * interval_ms) for idx, (timestamp, _) in enumerate(candles))


def _resolve_window_universe(
    settings: Settings,
    *,
    history_by_symbol: dict[str, list[tuple[int, float]]],
    start_ms: int,
    end_ms: int,
) -> list[str]:
    interval_ms = settings.ticker_interval_ms
    valid_symbols = [
        symbol
        for symbol in settings.universe
        if _has_complete_aligned_history(
            history_by_symbol.get(symbol, []),
            start_ms=start_ms,
            end_ms=end_ms,
            interval_ms=interval_ms,
        )
    ]
    missing = [symbol for symbol in settings.universe if symbol not in valid_symbols]
    if settings.backtest_universe_policy == "strict" and missing:
        raise MissingCandlesError(f"Missing window history for symbols: {', '.join(sorted(missing)[:10])}")
    if len(valid_symbols) < max(settings.backtest_min_active_universe, 1):
        raise MissingCandlesError(
            "Window-active universe too small: "
            f"{len(valid_symbols)} < {max(settings.backtest_min_active_universe, 1)}"
        )
    return valid_symbols


async def fetch_minute_replay_plan(
    client: BybitMarketDataClient,
    settings: Settings,
    replay_cycles: int,
) -> MinuteReplayPlan:
    confirmed_plan = await fetch_replay_plan(client, settings, replay_cycles, settings.tracked_symbols)
    first_replay_bar_ms = confirmed_plan.replay_timestamps[0]
    last_replay_bar_ms = confirmed_plan.replay_timestamps[-1]
    intrabar_interval_ms = interval_to_milliseconds(settings.backtest_intrabar_interval)
    end_ms = last_replay_bar_ms + settings.ticker_interval_ms
    expected_intrabar_candles = settings.ticker_interval_ms // intrabar_interval_ms
    semaphore = asyncio.Semaphore(settings.bootstrap_concurrency)

    async def _load_intrabar_symbol(symbol: str) -> tuple[str, dict[int, list[HistoricalCandle]]]:
        async with semaphore:
            minute_candles = await client.fetch_closed_ohlc_range(
                symbol=symbol,
                interval=settings.backtest_intrabar_interval,
                start_ms=first_replay_bar_ms,
                end_ms=end_ms,
            )
        grouped = _group_intrabar_candles(
            minute_candles,
            parent_interval_ms=settings.ticker_interval_ms,
        )
        for bucket_start_ms in confirmed_plan.replay_timestamps:
            bucket = grouped.get(bucket_start_ms)
            if bucket is None or len(bucket) != expected_intrabar_candles:
                raise MissingCandlesError(
                    f"Missing {settings.backtest_intrabar_interval}m intrabar candles for "
                    f"{symbol} bucket {bucket_start_ms}"
                )
        return symbol, grouped

    intrabar_by_symbol = dict(
        await asyncio.gather(*(_load_intrabar_symbol(symbol) for symbol in settings.tracked_symbols))
    )

    replay_days = math.ceil((end_ms - first_replay_bar_ms) / interval_to_milliseconds("D"))
    daily_limit = min(1000, settings.btc_daily_lookback + replay_days + 3)
    btcdom_warmup_ms = interval_to_milliseconds(settings.btcdom_interval) * (
        settings.btcdom_history_lookback + 2
    )
    btc_daily_history, btcdom_history = await asyncio.gather(
        client.fetch_closed_klines(
            symbol="BTCUSDT",
            interval="D",
            limit=daily_limit,
        ),
        client.fetch_btcdom_klines_range(
            start_ms=first_replay_bar_ms - btcdom_warmup_ms,
            end_ms=end_ms,
        ),
    )
    return MinuteReplayPlan(
        confirmed_plan=confirmed_plan,
        intrabar_by_symbol=intrabar_by_symbol,
        btc_daily_history=btc_daily_history,
        btcdom_history=btcdom_history,
        active_universe=list(settings.universe),
    )


async def fetch_replay_plan_for_window(
    client: BybitMarketDataClient,
    settings: Settings,
    replay_cycles: int,
    *,
    replay_end_ms: int,
    symbols: list[str] | None = None,
) -> ReplayPlan:
    symbols = settings.tracked_symbols if symbols is None else symbols
    interval_ms = settings.ticker_interval_ms
    replay_end_ms = _align_down(replay_end_ms, interval_ms)
    replay_start_ms = replay_end_ms - (replay_cycles * interval_ms)
    history_start_ms = replay_start_ms - (settings.state_window * interval_ms)
    if replay_cycles <= 0:
        raise ValueError("replay_cycles must be positive")

    async def _load_symbol(symbol: str) -> tuple[str, list[tuple[int, float]]]:
        candles = await client.fetch_closed_klines_range(
            symbol=symbol,
            interval=settings.candle_interval,
            start_ms=history_start_ms,
            end_ms=replay_end_ms,
        )
        return symbol, candles

    raw_history_by_symbol = dict(await asyncio.gather(*(_load_symbol(symbol) for symbol in symbols)))
    active_universe = _resolve_window_universe(
        settings,
        history_by_symbol=raw_history_by_symbol,
        start_ms=history_start_ms,
        end_ms=replay_end_ms,
    )
    history_symbols = active_universe + ["BTCUSDT"]
    history_by_symbol = {symbol: raw_history_by_symbol[symbol] for symbol in history_symbols}
    day_ms = interval_to_milliseconds("D")
    btc_daily_history = await client.fetch_closed_klines_range(
        symbol="BTCUSDT",
        interval="D",
        start_ms=replay_start_ms - ((settings.btc_daily_lookback + 5) * day_ms),
        end_ms=replay_end_ms,
    )
    return build_replay_plan(
        history_by_symbol=history_by_symbol,
        btc_daily_history=btc_daily_history,
        state_window=settings.state_window,
        replay_cycles=replay_cycles,
    )


async def fetch_minute_replay_plan_for_window(
    client: BybitMarketDataClient,
    settings: Settings,
    replay_cycles: int,
    *,
    replay_end_ms: int,
) -> MinuteReplayPlan:
    confirmed_plan = await fetch_replay_plan_for_window(
        client,
        settings,
        replay_cycles,
        replay_end_ms=replay_end_ms,
    )
    active_universe = [
        symbol for symbol in confirmed_plan.history_by_symbol if symbol != "BTCUSDT"
    ]
    first_replay_bar_ms = confirmed_plan.replay_timestamps[0]
    last_replay_bar_ms = confirmed_plan.replay_timestamps[-1]
    intrabar_interval_ms = interval_to_milliseconds(settings.backtest_intrabar_interval)
    end_ms = last_replay_bar_ms + settings.ticker_interval_ms
    expected_intrabar_candles = settings.ticker_interval_ms // intrabar_interval_ms
    semaphore = asyncio.Semaphore(settings.bootstrap_concurrency)

    async def _load_intrabar_symbol(symbol: str) -> tuple[str, dict[int, list[HistoricalCandle]]]:
        async with semaphore:
            minute_candles = await client.fetch_closed_ohlc_range(
                symbol=symbol,
                interval=settings.backtest_intrabar_interval,
                start_ms=first_replay_bar_ms,
                end_ms=end_ms,
            )
        grouped = _group_intrabar_candles(
            minute_candles,
            parent_interval_ms=settings.ticker_interval_ms,
        )
        for bucket_start_ms in confirmed_plan.replay_timestamps:
            bucket = grouped.get(bucket_start_ms)
            if bucket is None or len(bucket) != expected_intrabar_candles:
                raise MissingCandlesError(
                    f"Missing {settings.backtest_intrabar_interval}m intrabar candles for "
                    f"{symbol} bucket {bucket_start_ms}"
                )
        return symbol, grouped

    intrabar_by_symbol = dict(
        await asyncio.gather(
            *(_load_intrabar_symbol(symbol) for symbol in (active_universe + ["BTCUSDT"]))
        )
    )
    day_ms = interval_to_milliseconds("D")
    btc_daily_history = await client.fetch_closed_klines_range(
        symbol="BTCUSDT",
        interval="D",
        start_ms=first_replay_bar_ms - ((settings.btc_daily_lookback + 5) * day_ms),
        end_ms=end_ms,
    )
    btcdom_warmup_ms = interval_to_milliseconds(settings.btcdom_interval) * (
        settings.btcdom_history_lookback + 2
    )
    btcdom_history = await client.fetch_btcdom_klines_range(
        start_ms=first_replay_bar_ms - btcdom_warmup_ms,
        end_ms=end_ms,
    )
    return MinuteReplayPlan(
        confirmed_plan=confirmed_plan,
        intrabar_by_symbol=intrabar_by_symbol,
        btc_daily_history=btc_daily_history,
        btcdom_history=btcdom_history,
        active_universe=active_universe,
    )


async def prefetch_backtest_cache(
    client: BybitMarketDataClient,
    settings: Settings,
    *,
    lookback_days: int,
    end_date: str | None = None,
) -> PrefetchSummary:
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if not settings.backtest_cache_enabled:
        raise ValueError("BACKTEST_CACHE_ENABLED must be true for prefetch mode")

    day_ms = interval_to_milliseconds("D")
    interval_ms = settings.ticker_interval_ms
    end_ms = _resolve_end_ms(settings, end_date)
    price_start_ms = end_ms - (lookback_days * day_ms) - (settings.state_window * interval_ms)
    intrabar_start_ms = end_ms - (lookback_days * day_ms)
    btc_daily_start_ms = end_ms - ((lookback_days + settings.btc_daily_lookback + 5) * day_ms)
    btcdom_start_ms = end_ms - (lookback_days * day_ms) - (
        interval_to_milliseconds(settings.btcdom_interval)
        * (settings.btcdom_history_lookback + 2)
    )
    semaphore = asyncio.Semaphore(settings.bootstrap_concurrency)
    symbol_intervals: list[tuple[str, str, int]] = []
    for symbol in settings.tracked_symbols:
        symbol_intervals.append((symbol, settings.candle_interval, price_start_ms))
        if settings.backtest_intrabar_interval != settings.candle_interval:
            symbol_intervals.append((symbol, settings.backtest_intrabar_interval, intrabar_start_ms))

    async def _prefetch_symbol_interval(symbol: str, interval: str, start_ms: int) -> int:
        async with semaphore:
            candles = await client.fetch_closed_ohlc_range(
                symbol=symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        return len(candles)

    bybit_counts = await asyncio.gather(
        *(
            _prefetch_symbol_interval(symbol, interval, start_ms)
            for symbol, interval, start_ms in symbol_intervals
        )
    )
    btc_daily_history, btcdom_history = await asyncio.gather(
        client.fetch_closed_ohlc_range(
            symbol="BTCUSDT",
            interval="D",
            start_ms=btc_daily_start_ms,
            end_ms=end_ms,
        ),
        client.fetch_btcdom_klines_range(
            start_ms=btcdom_start_ms,
            end_ms=end_ms,
        ),
    )
    return PrefetchSummary(
        cache_path=str(Path(settings.backtest_cache_path).expanduser()),
        lookback_days=lookback_days,
        start_utc=_timestamp_iso(intrabar_start_ms),
        end_utc=_timestamp_iso(end_ms),
        tracked_symbols=len(settings.tracked_symbols),
        bybit_symbol_intervals=len(symbol_intervals),
        bybit_candles=sum(bybit_counts),
        btc_daily_candles=len(btc_daily_history),
        btcdom_candles=len(btcdom_history),
        cache_rows=client.cached_candle_count(),
    )


def format_prefetch_summary(summary: PrefetchSummary) -> str:
    return "\n".join(
        [
            "Backtest cache prefetch",
            f"  cache_path={summary.cache_path}",
            f"  lookback_days={summary.lookback_days}",
            f"  start_utc={summary.start_utc}",
            f"  end_utc={summary.end_utc}",
            f"  tracked_symbols={summary.tracked_symbols}",
            f"  bybit_symbol_intervals={summary.bybit_symbol_intervals}",
            f"  bybit_candles={summary.bybit_candles}",
            f"  btc_daily_candles={summary.btc_daily_candles}",
            f"  btcdom_candles={summary.btcdom_candles}",
            f"  cache_rows={summary.cache_rows}",
        ]
    )


def _timestamp_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date().isoformat()


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _set_btc_daily_state(
    state: MarketState,
    btc_daily_history: list[tuple[int, float]],
    *,
    current_timestamp_ms: int,
) -> None:
    day_ms = interval_to_milliseconds("D")
    closed = [
        close
        for start_ms, close in btc_daily_history
        if start_ms + day_ms <= current_timestamp_ms
    ]
    if len(closed) >= state.settings.btc_daily_lookback:
        state.global_state.btc_daily_closes = np.asarray(
            closed[-state.settings.btc_daily_lookback :],
            dtype=float,
        )


def _set_btcdom_state(
    state: MarketState,
    btcdom_history: list[tuple[int, float]],
    *,
    current_timestamp_ms: int,
) -> None:
    interval_ms = interval_to_milliseconds(state.settings.btcdom_interval)
    closed = [
        close
        for start_ms, close in btcdom_history
        if start_ms + interval_ms <= current_timestamp_ms
    ]
    if len(closed) >= state.settings.btcdom_history_lookback:
        state.global_state.btcdom_closes = np.asarray(
            closed[-state.settings.btcdom_history_lookback :],
            dtype=float,
        )
        state.global_state.btc_dominance_series = state.global_state.btcdom_closes
        state.refresh_btcdom_state()


class HistoricalBacktestSimulator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.positions: dict[str, SimulatedPosition] = {}
        self.trades: list[BacktestTrade] = []
        self.equity_curve: list[EquitySnapshot] = []
        self.realized_gross_pnl_usd = 0.0
        self.total_fees_usd = 0.0
        self.total_slippage_usd = 0.0
        self.peak_equity_usd = settings.backtest_starting_equity_usd
        self.max_drawdown_pct = 0.0
        self.entry_ready_signals = 0
        self.entries_filled = 0
        self.skipped_duplicate_ticker = 0
        self.skipped_max_open_positions = 0
        self.skipped_cluster_limit = 0
        self.skipped_max_entries_per_rebalance = 0
        self.skipped_daily_stop_losses = 0
        self.skipped_gross_exposure_cap = 0
        self.skipped_too_small = 0
        self.max_open_positions_observed = 0
        self.stop_loss_exits_by_day: dict[str, int] = defaultdict(int)
        self.post_exit_trackers: list[PostExitTracker] = []

    @property
    def slippage_rate(self) -> float:
        return self.settings.backtest_slippage_bps / 10_000.0

    def _entry_fill_price(self, raw_price: float) -> float:
        return raw_price * (1.0 + self.slippage_rate)

    def _exit_fill_price(self, raw_price: float) -> float:
        return raw_price * (1.0 - self.slippage_rate)

    def _unrealized_pnl_usd(self, mark_prices: dict[str, float]) -> float:
        pnl = 0.0
        for ticker, position in self.positions.items():
            mark_price = mark_prices.get(ticker)
            if mark_price is None:
                continue
            pnl += (mark_price - position.entry_price) * position.quantity
        return pnl

    def current_equity_usd(self, mark_prices: dict[str, float]) -> float:
        return (
            self.settings.backtest_starting_equity_usd
            + self.realized_gross_pnl_usd
            - self.total_fees_usd
            + self._unrealized_pnl_usd(mark_prices)
        )

    def current_gross_exposure_usd(self, mark_prices: dict[str, float]) -> float:
        exposure = 0.0
        for ticker, position in self.positions.items():
            mark_price = mark_prices.get(ticker, position.entry_price)
            exposure += abs(mark_price * position.quantity)
        return exposure

    def _open_cluster_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for position in self.positions.values():
            cluster = position.cluster_label or f"manual:{ticker_cluster(position.ticker)}"
            counts[cluster] = counts.get(cluster, 0) + 1
        return counts

    def _record_snapshot(self, timestamp_ms: int, mark_prices: dict[str, float]) -> None:
        equity_usd = self.current_equity_usd(mark_prices)
        self.peak_equity_usd = max(self.peak_equity_usd, equity_usd)
        drawdown_pct = 0.0
        if self.peak_equity_usd > 0:
            drawdown_pct = max(0.0, (self.peak_equity_usd - equity_usd) / self.peak_equity_usd)
        self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown_pct)
        snapshot = EquitySnapshot(
            timestamp=_timestamp_iso(timestamp_ms),
            equity_usd=equity_usd,
            gross_exposure_usd=self.current_gross_exposure_usd(mark_prices),
            realized_gross_pnl_usd=self.realized_gross_pnl_usd,
            realized_net_pnl_usd=self.realized_gross_pnl_usd - self.total_fees_usd,
            unrealized_pnl_usd=self._unrealized_pnl_usd(mark_prices),
            fees_usd=self.total_fees_usd,
            open_positions=len(self.positions),
            drawdown_pct=drawdown_pct,
        )
        self.equity_curve.append(snapshot)
        self.max_open_positions_observed = max(self.max_open_positions_observed, len(self.positions))

    def _update_position_excursions(self, position: SimulatedPosition, candle: HistoricalCandle) -> None:
        favorable_pct = max((candle.high_price / position.entry_price) - 1.0, 0.0)
        adverse_pct = max(1.0 - (candle.low_price / position.entry_price), 0.0)
        if favorable_pct >= position.mfe_pct:
            position.mfe_pct = favorable_pct
            position.peak_favorable_price = candle.high_price
        if adverse_pct >= position.mae_pct:
            position.mae_pct = adverse_pct
            position.peak_adverse_price = candle.low_price

    def _track_post_exit_trade(self, trade_index: int, ticker: str, exit_price: float) -> None:
        if self.settings.analytics_post_exit_bars <= 0:
            return
        self.post_exit_trackers.append(
            PostExitTracker(
                trade_index=trade_index,
                ticker=ticker,
                exit_price=exit_price,
                bars_remaining=self.settings.analytics_post_exit_bars,
            )
        )

    def update_post_exit_trackers(
        self,
        intrabar_candles: dict[str, HistoricalCandle],
    ) -> None:
        if not self.post_exit_trackers:
            return
        remaining: list[PostExitTracker] = []
        for tracker in self.post_exit_trackers:
            candle = intrabar_candles.get(tracker.ticker)
            if candle is None:
                remaining.append(tracker)
                continue
            favorable_pct = max((candle.high_price / tracker.exit_price) - 1.0, 0.0)
            adverse_pct = min((candle.low_price / tracker.exit_price) - 1.0, 0.0)
            tracker.best_pct = max(tracker.best_pct, favorable_pct)
            tracker.worst_pct = min(tracker.worst_pct, adverse_pct)
            tracker.close_prices.append(candle.close_price)
            tracker.bars_remaining -= 1
            if tracker.bars_remaining <= 0:
                self._finalize_post_exit_tracker(tracker)
            else:
                remaining.append(tracker)
        self.post_exit_trackers = remaining

    def _finalize_post_exit_tracker(self, tracker: PostExitTracker) -> None:
        trade = self.trades[tracker.trade_index]
        trade.post_exit_best_pct = tracker.best_pct
        trade.post_exit_worst_pct = tracker.worst_pct
        if len(tracker.close_prices) >= 2:
            returns = log_returns(np.asarray(tracker.close_prices, dtype=float))
            trade.volatility_pct = float(np.std(returns, ddof=0)) if returns.size > 0 else 0.0
        else:
            trade.volatility_pct = None

    def finalize_post_exit_trackers(self) -> None:
        for tracker in self.post_exit_trackers:
            if len(tracker.close_prices) > 1:
                self._finalize_post_exit_tracker(tracker)
        self.post_exit_trackers.clear()

    def _close_position(
        self,
        *,
        ticker: str,
        raw_exit_price: float,
        timestamp_ms: int,
        exit_reason: str,
        intrabar_candle: HistoricalCandle | None = None,
    ) -> None:
        position = self.positions.pop(ticker)
        exit_price = self._exit_fill_price(raw_exit_price)
        exit_fee_usd = exit_price * position.quantity * self.settings.backtest_fee_rate
        exit_slippage_usd = max(raw_exit_price - exit_price, 0.0) * position.quantity
        gross_pnl_usd = (exit_price - position.entry_price) * position.quantity
        net_pnl_usd = gross_pnl_usd - position.entry_fee_usd - exit_fee_usd
        holding_minutes = (timestamp_ms - position.opened_at_ms) / 60_000.0
        self.realized_gross_pnl_usd += gross_pnl_usd
        self.total_fees_usd += exit_fee_usd
        self.total_slippage_usd += exit_slippage_usd
        if intrabar_candle is not None:
            self._update_position_excursions(position, intrabar_candle)
        trade = BacktestTrade(
            ticker=ticker,
            side="LONG",
            opened_at=_timestamp_iso(position.opened_at_ms),
            closed_at=_timestamp_iso(timestamp_ms),
            entry_stage=position.entry_stage,
            entry_signal_kind=position.entry_signal_kind,
            cluster_label=position.cluster_label,
            entry_diagnostics=position.entry_diagnostics,
            exit_reason=exit_reason,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            notional_usd=position.notional_usd,
            gross_pnl_usd=gross_pnl_usd,
            net_pnl_usd=net_pnl_usd,
            pnl_pct=(exit_price / position.entry_price) - 1.0 if position.entry_price > 0 else 0.0,
            entry_fee_usd=position.entry_fee_usd,
            exit_fee_usd=exit_fee_usd,
            entry_slippage_usd=position.entry_slippage_usd,
            exit_slippage_usd=exit_slippage_usd,
            holding_minutes=holding_minutes,
            mfe_pct=position.mfe_pct,
            mae_pct=position.mae_pct,
        )
        self.trades.append(trade)
        self._track_post_exit_trade(len(self.trades) - 1, ticker, exit_price)
        if exit_reason == "stop_loss":
            self.stop_loss_exits_by_day[_utc_day(timestamp_ms)] += 1

    def process_intrabar_exits(
        self,
        *,
        timestamp_ms: int,
        intrabar_candles: dict[str, HistoricalCandle],
        mark_prices: dict[str, float],
    ) -> set[str]:
        closed_tickers: set[str] = set()
        self.update_post_exit_trackers(intrabar_candles)
        for ticker in list(self.positions):
            position = self.positions.get(ticker)
            candle = intrabar_candles.get(ticker)
            if position is None or candle is None:
                continue
            self._update_position_excursions(position, candle)
            hit_stop = candle.low_price <= position.stop_loss_price
            hit_take_profit = candle.high_price >= position.take_profit_price
            if hit_stop and hit_take_profit:
                self._close_position(
                    ticker=ticker,
                    raw_exit_price=position.stop_loss_price,
                    timestamp_ms=timestamp_ms,
                    exit_reason="stop_loss",
                    intrabar_candle=candle,
                )
                closed_tickers.add(ticker)
                continue
            if hit_stop:
                self._close_position(
                    ticker=ticker,
                    raw_exit_price=position.stop_loss_price,
                    timestamp_ms=timestamp_ms,
                    exit_reason="stop_loss",
                    intrabar_candle=candle,
                )
                closed_tickers.add(ticker)
                continue
            if hit_take_profit:
                self._close_position(
                    ticker=ticker,
                    raw_exit_price=position.take_profit_price,
                    timestamp_ms=timestamp_ms,
                    exit_reason="take_profit",
                    intrabar_candle=candle,
                )
                closed_tickers.add(ticker)
        self._record_snapshot(timestamp_ms, mark_prices)
        return closed_tickers

    def process_entries(
        self,
        *,
        timestamp_ms: int,
        ranked_signals: list[RankedSignal],
        mark_prices: dict[str, float],
        blocked_tickers: set[str] | None = None,
    ) -> None:
        blocked_tickers = blocked_tickers or set()
        candidates = [signal for signal in ranked_signals if signal.signal_kind == "entry_ready"]
        candidates.sort(key=lambda signal: signal.rank)
        self.entry_ready_signals += len(candidates)
        opened_this_cycle = 0
        cluster_counts = self._open_cluster_counts()
        for signal in candidates:
            if signal.ticker in blocked_tickers:
                self.skipped_duplicate_ticker += 1
                continue
            if signal.ticker in self.positions:
                self.skipped_duplicate_ticker += 1
                continue
            if (
                self.settings.max_open_positions > 0
                and len(self.positions) >= self.settings.max_open_positions
            ):
                self.skipped_max_open_positions += 1
                continue
            cluster = signal.cluster_label or f"manual:{ticker_cluster(signal.ticker)}"
            if (
                self.settings.max_positions_per_cluster > 0
                and cluster_counts.get(cluster, 0) >= self.settings.max_positions_per_cluster
            ):
                self.skipped_cluster_limit += 1
                continue
            if (
                self.settings.max_entries_per_rebalance > 0
                and opened_this_cycle >= self.settings.max_entries_per_rebalance
            ):
                self.skipped_max_entries_per_rebalance += 1
                continue
            if (
                self.settings.max_daily_stop_losses > 0
                and self.stop_loss_exits_by_day[_utc_day(timestamp_ms)]
                >= self.settings.max_daily_stop_losses
            ):
                self.skipped_daily_stop_losses += 1
                continue
            equity_usd = self.current_equity_usd(mark_prices)
            gross_exposure = self.current_gross_exposure_usd(mark_prices)
            gross_cap = equity_usd * self.settings.backtest_max_gross_exposure_multiple
            available_notional = max(gross_cap - gross_exposure, 0.0)
            risk_based_notional = equity_usd * self.settings.risk_per_trade_pct / self.settings.stop_loss_pct
            target_notional = min(risk_based_notional, available_notional)
            if target_notional <= 0:
                self.skipped_gross_exposure_cap += 1
                continue
            raw_entry_price = signal.current_price
            entry_price = self._entry_fill_price(raw_entry_price)
            quantity = target_notional / entry_price if entry_price > 0 else 0.0
            if quantity <= 0 or target_notional < 1e-8:
                self.skipped_too_small += 1
                continue
            entry_fee_usd = target_notional * self.settings.backtest_fee_rate
            entry_slippage_usd = max(entry_price - raw_entry_price, 0.0) * quantity
            self.total_fees_usd += entry_fee_usd
            self.total_slippage_usd += entry_slippage_usd
            self.positions[signal.ticker] = SimulatedPosition(
                ticker=signal.ticker,
                quantity=quantity,
                entry_price=entry_price,
                raw_entry_price=raw_entry_price,
                notional_usd=target_notional,
                opened_at_ms=timestamp_ms,
                entry_stage=signal.stage,
                entry_signal_kind=signal.signal_kind,
                cluster_label=cluster,
                entry_diagnostics=signal.entry_diagnostics,
                take_profit_price=entry_price * (1.0 + self.settings.take_profit_pct),
                stop_loss_price=entry_price * (1.0 - self.settings.stop_loss_pct),
                entry_fee_usd=entry_fee_usd,
                entry_slippage_usd=entry_slippage_usd,
            )
            self.entries_filled += 1
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
            opened_this_cycle += 1
        self._record_snapshot(timestamp_ms, mark_prices)

    def process_confirmed(
        self,
        *,
        timestamp_ms: int,
        confirmed_prices: dict[str, float],
    ) -> None:
        self._record_snapshot(timestamp_ms, confirmed_prices)

    def force_close_all(self, *, timestamp_ms: int, mark_prices: dict[str, float]) -> None:
        for ticker in list(self.positions):
            raw_exit_price = mark_prices.get(ticker)
            if raw_exit_price is None:
                continue
            self._close_position(
                ticker=ticker,
                raw_exit_price=raw_exit_price,
                timestamp_ms=timestamp_ms,
                exit_reason="end_of_backtest",
            )
        self._record_snapshot(timestamp_ms, mark_prices)
        self.finalize_post_exit_trackers()


def _daily_summaries(
    trades: list[BacktestTrade],
    equity_curve: list[EquitySnapshot],
    starting_equity_usd: float,
) -> list[DailyBacktestSummary]:
    grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.closed_at[:10]].append(trade)
    closing_equity_by_day: dict[str, float] = {}
    for snapshot in equity_curve:
        closing_equity_by_day[snapshot.timestamp[:10]] = snapshot.equity_usd
    summaries: list[DailyBacktestSummary] = []
    previous_equity = starting_equity_usd
    for day in sorted(set(list(grouped) + list(closing_equity_by_day))):
        day_trades = grouped.get(day, [])
        wins = sum(1 for trade in day_trades if trade.net_pnl_usd > 0)
        losses = sum(1 for trade in day_trades if trade.net_pnl_usd < 0)
        gross_pnl_usd = sum(trade.gross_pnl_usd for trade in day_trades)
        net_pnl_usd = sum(trade.net_pnl_usd for trade in day_trades)
        fees_usd = sum(trade.entry_fee_usd + trade.exit_fee_usd for trade in day_trades)
        ending_equity = closing_equity_by_day.get(day, previous_equity)
        return_pct = (
            ((ending_equity / previous_equity) - 1.0)
            if previous_equity > 0
            else 0.0
        )
        summaries.append(
            DailyBacktestSummary(
                day=day,
                trades=len(day_trades),
                wins=wins,
                losses=losses,
                gross_pnl_usd=gross_pnl_usd,
                net_pnl_usd=net_pnl_usd,
                fees_usd=fees_usd,
                return_pct=return_pct,
            )
        )
        previous_equity = ending_equity
    return summaries


def _ticker_summaries(trades: list[BacktestTrade]) -> list[TickerBacktestSummary]:
    grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.ticker].append(trade)
    rows: list[TickerBacktestSummary] = []
    for ticker, ticker_trades in grouped.items():
        wins = sum(1 for trade in ticker_trades if trade.net_pnl_usd > 0)
        losses = sum(1 for trade in ticker_trades if trade.net_pnl_usd < 0)
        rows.append(
            TickerBacktestSummary(
                ticker=ticker,
                trades=len(ticker_trades),
                wins=wins,
                losses=losses,
                gross_pnl_usd=sum(trade.gross_pnl_usd for trade in ticker_trades),
                net_pnl_usd=sum(trade.net_pnl_usd for trade in ticker_trades),
                avg_pnl_pct=_mean([trade.pnl_pct for trade in ticker_trades]) or 0.0,
            )
        )
    rows.sort(key=lambda item: item.net_pnl_usd, reverse=True)
    return rows


def _profit_factor(trades: list[BacktestTrade]) -> float | None:
    gains = sum(trade.net_pnl_usd for trade in trades if trade.net_pnl_usd > 0)
    losses = -sum(trade.net_pnl_usd for trade in trades if trade.net_pnl_usd < 0)
    if losses == 0:
        return None if gains == 0 else float("inf")
    return gains / losses


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def export_comprehensive_backtest(
    result: ComprehensiveBacktestResult,
    *,
    export_dir: str,
) -> None:
    export_path = Path(export_dir).expanduser()
    export_path.mkdir(parents=True, exist_ok=True)
    _write_csv(export_path / "backtest_trades.csv", [asdict(trade) for trade in result.trades])
    _write_csv(export_path / "backtest_equity_curve.csv", [asdict(row) for row in result.equity_curve])
    _write_csv(export_path / "backtest_daily.csv", [asdict(row) for row in result.summary.daily])
    _write_csv(export_path / "backtest_tickers.csv", [asdict(row) for row in result.summary.tickers])


def format_comprehensive_backtest(result: ComprehensiveBacktestResult) -> str:
    summary = result.summary
    profit_factor = "inf" if summary.profit_factor == float("inf") else (
        f"{summary.profit_factor:.3f}" if summary.profit_factor is not None else "n/a"
    )
    lines = [
        f"Backtest mode: {summary.mode}",
        f"Starting equity: {summary.starting_equity_usd:.2f}",
        f"Ending equity: {summary.ending_equity_usd:.2f}",
        f"Total return: {summary.total_return_pct * 100:.2f}%",
        f"Gross PnL USD: {summary.gross_pnl_usd:.2f}",
        f"Net PnL USD: {summary.net_pnl_usd:.2f}",
        f"Fees USD: {summary.fees_usd:.2f}",
        f"Slippage USD: {summary.slippage_usd:.2f}",
        f"Max drawdown: {summary.max_drawdown_pct * 100:.2f}%",
        (
            f"Trades: {summary.trade_count} wins={summary.wins} losses={summary.losses} "
            f"win_rate={summary.win_rate * 100:.1f}%"
        ),
        f"Profit factor: {profit_factor}",
        f"Avg win USD: {summary.avg_win_usd:.2f}" if summary.avg_win_usd is not None else "Avg win USD: n/a",
        f"Avg loss USD: {summary.avg_loss_usd:.2f}" if summary.avg_loss_usd is not None else "Avg loss USD: n/a",
        f"Expectancy USD: {summary.expectancy_usd:.2f}" if summary.expectancy_usd is not None else "Expectancy USD: n/a",
        (
            f"Avg holding min: {summary.avg_holding_minutes:.1f} "
            f"avg_mfe_pct={summary.avg_mfe_pct * 100:.2f}% avg_mae_pct={summary.avg_mae_pct * 100:.2f}%"
            if summary.avg_holding_minutes is not None and summary.avg_mfe_pct is not None and summary.avg_mae_pct is not None
            else "Avg holding / excursions: n/a"
        ),
        (
            f"Post-exit: avg_best_pct={summary.avg_post_exit_best_pct * 100:.2f}% "
            f"avg_worst_pct={summary.avg_post_exit_worst_pct * 100:.2f}% "
            f"avg_volatility_pct={summary.avg_volatility_pct * 100:.2f}%"
            if summary.avg_post_exit_best_pct is not None
            and summary.avg_post_exit_worst_pct is not None
            and summary.avg_volatility_pct is not None
            else "Post-exit follow-through: n/a"
        ),
        (
            f"Exits: take_profit={summary.take_profits} stop_loss={summary.stop_losses} "
            f"forced={summary.forced_exits}"
        ),
        (
            f"Entry flow: entry_ready_signals={summary.entry_ready_signals} filled={summary.entries_filled} "
            f"dup={summary.skipped_duplicate_ticker} max_open={summary.skipped_max_open_positions} "
            f"cluster_cap={summary.skipped_cluster_limit} "
            f"batch_cap={summary.skipped_max_entries_per_rebalance} daily_stop={summary.skipped_daily_stop_losses} "
            f"gross_cap={summary.skipped_gross_exposure_cap} too_small={summary.skipped_too_small}"
        ),
        f"Max open positions observed: {summary.max_open_positions_observed}",
        "",
        "Signal report",
        format_report(summary.signal_summary),
    ]
    if summary.daily:
        lines.append("")
        lines.append("Daily results")
        for row in summary.daily:
            lines.append(
                f"  {row.day}: trades={row.trades} wins={row.wins} losses={row.losses} "
                f"gross_pnl_usd={row.gross_pnl_usd:.2f} net_pnl_usd={row.net_pnl_usd:.2f} "
                f"fees_usd={row.fees_usd:.2f} return_pct={row.return_pct * 100:.2f}%"
            )
    if summary.tickers:
        lines.append("")
        lines.append("Top tickers")
        for row in summary.tickers[:10]:
            lines.append(
                f"  {row.ticker}: trades={row.trades} wins={row.wins} losses={row.losses} "
                f"net_pnl_usd={row.net_pnl_usd:.2f} avg_pnl_pct={row.avg_pnl_pct * 100:.2f}%"
            )
    return "\n".join(lines)


def _format_comprehensive_comparison(
    with_filter: ComprehensiveBacktestResult,
    without_filter: ComprehensiveBacktestResult,
) -> str:
    left = with_filter.summary
    right = without_filter.summary
    return "\n".join(
        [
            "Backtest comparison",
            f"  filter_on_db={with_filter.database_path}",
            f"  filter_off_db={without_filter.database_path}",
            f"  trades: on={left.trade_count} off={right.trade_count}",
            f"  win_rate_pct: on={left.win_rate * 100:.1f} off={right.win_rate * 100:.1f}",
            f"  total_return_pct: on={left.total_return_pct * 100:.2f} off={right.total_return_pct * 100:.2f}",
            f"  net_pnl_usd: on={left.net_pnl_usd:.2f} off={right.net_pnl_usd:.2f}",
            f"  fees_usd: on={left.fees_usd:.2f} off={right.fees_usd:.2f}",
            f"  max_drawdown_pct: on={left.max_drawdown_pct * 100:.2f} off={right.max_drawdown_pct * 100:.2f}",
            f"  stop_losses: on={left.stop_losses} off={right.stop_losses}",
            f"  take_profits: on={left.take_profits} off={right.take_profits}",
            f"  entry_ready_signals: on={left.entry_ready_signals} off={right.entry_ready_signals}",
            f"  entries_filled: on={left.entries_filled} off={right.entries_filled}",
        ]
    )


async def run_comprehensive_backtest_plan(
    settings: Settings,
    plan: MinuteReplayPlan,
    *,
    sqlite_path: str,
    intraday_regime_filter_enabled: bool | None = None,
) -> ComprehensiveBacktestResult:
    backtest_settings = _build_comprehensive_settings(
        settings,
        sqlite_path=sqlite_path,
        intraday_regime_filter_enabled=intraday_regime_filter_enabled,
    )
    if plan.active_universe is not None:
        backtest_settings = replace(backtest_settings, universe=list(plan.active_universe))
    database_file = Path(backtest_settings.sqlite_path)
    if database_file.exists():
        database_file.unlink()

    state = MarketState(settings=backtest_settings)
    for symbol, candles in plan.confirmed_plan.history_by_symbol.items():
        state.replace_history(symbol, candles[: backtest_settings.state_window])
    initial_ts = plan.confirmed_plan.replay_timestamps[0]
    _set_btc_daily_state(
        state,
        plan.btc_daily_history,
        current_timestamp_ms=initial_ts,
    )
    _set_btcdom_state(
        state,
        plan.btcdom_history,
        current_timestamp_ms=initial_ts,
    )

    database = SignalDatabase(backtest_settings.sqlite_path)
    await database.initialize()
    notifier = NullNotifier()
    signal_engine = SignalEngine(
        settings=backtest_settings,
        state=state,
        database=database,
        notifier=notifier,
    )
    simulator = HistoricalBacktestSimulator(backtest_settings)
    intrabar_interval_ms = interval_to_milliseconds(backtest_settings.backtest_intrabar_interval)

    try:
        for offset, bar_start_ms in enumerate(plan.confirmed_plan.replay_timestamps):
            history_index = backtest_settings.state_window + offset
            bucket_minutes = {
                symbol: plan.intrabar_by_symbol[symbol][bar_start_ms]
                for symbol in backtest_settings.tracked_symbols
            }
            expected_bucket_length = len(next(iter(bucket_minutes.values())))
            for minute_index in range(expected_bucket_length):
                intrabar_candles = {
                    symbol: candles[minute_index]
                    for symbol, candles in bucket_minutes.items()
                }
                minute_close_ms = _minute_close_timestamp(
                    next(iter(intrabar_candles.values())),
                    intrabar_interval_ms,
                )
                for symbol, candle in intrabar_candles.items():
                    updated = state.update_provisional(symbol, bar_start_ms, candle.close_price)
                    provisional = state.provisional_state.get(symbol)
                    unchanged_provisional = bool(
                        provisional is not None
                        and provisional.start_time_ms == bar_start_ms
                        and provisional.close_price == candle.close_price
                    )
                    if not updated and not unchanged_provisional:
                        raise RuntimeError(
                            f"Intrabar replay failed for {symbol} bar {bar_start_ms} minute {candle.start_time_ms}"
                        )
                _set_btc_daily_state(
                    state,
                    plan.btc_daily_history,
                    current_timestamp_ms=minute_close_ms,
                )
                _set_btcdom_state(
                    state,
                    plan.btcdom_history,
                    current_timestamp_ms=minute_close_ms,
                )
                emerging_signals = await signal_engine.process(
                    cycle_time_ms=minute_close_ms,
                    stage="emerging",
                )
                mark_prices = {
                    symbol: candle.close_price
                    for symbol, candle in intrabar_candles.items()
                }
                closed_tickers = simulator.process_intrabar_exits(
                    timestamp_ms=minute_close_ms,
                    intrabar_candles=intrabar_candles,
                    mark_prices=mark_prices,
                )
                simulator.process_entries(
                    timestamp_ms=minute_close_ms,
                    ranked_signals=emerging_signals,
                    mark_prices=mark_prices,
                    blocked_tickers=closed_tickers,
                )

            confirmed_prices: dict[str, float] = {}
            confirmed_timestamp_ms = bar_start_ms + backtest_settings.ticker_interval_ms
            for symbol, candles in plan.confirmed_plan.history_by_symbol.items():
                candle_start_ms, close_price = candles[history_index]
                if candle_start_ms != bar_start_ms:
                    raise MissingCandlesError(
                        f"{symbol} confirmed replay candle misaligned at {history_index}"
                    )
                appended = state.append_close(symbol, candle_start_ms, close_price)
                if not appended:
                    raise RuntimeError(
                        f"Confirmed replay failed to append {symbol} candle {candle_start_ms}"
                    )
                confirmed_prices[symbol] = close_price
            _set_btc_daily_state(
                state,
                plan.btc_daily_history,
                current_timestamp_ms=confirmed_timestamp_ms,
            )
            _set_btcdom_state(
                state,
                plan.btcdom_history,
                current_timestamp_ms=confirmed_timestamp_ms,
            )
            simulator.process_confirmed(
                timestamp_ms=confirmed_timestamp_ms,
                confirmed_prices=confirmed_prices,
            )

        final_mark_prices = {
            symbol: candles[-1].close_price
            for symbol, candles in bucket_minutes.items()
        }
        simulator.force_close_all(
            timestamp_ms=plan.confirmed_plan.replay_timestamps[-1] + backtest_settings.ticker_interval_ms,
            mark_prices=final_mark_prices,
        )
    finally:
        database.close()

    signal_summary = (
        _empty_report_summary()
        if backtest_settings.backtest_research_fast
        else load_report_summary(backtest_settings.sqlite_path, top_n=10)
    )
    wins = [trade.net_pnl_usd for trade in simulator.trades if trade.net_pnl_usd > 0]
    losses = [trade.net_pnl_usd for trade in simulator.trades if trade.net_pnl_usd < 0]
    ending_equity_usd = (
        simulator.equity_curve[-1].equity_usd
        if simulator.equity_curve
        else backtest_settings.backtest_starting_equity_usd
    )
    daily = _daily_summaries(
        simulator.trades,
        simulator.equity_curve,
        backtest_settings.backtest_starting_equity_usd,
    )
    tickers = _ticker_summaries(simulator.trades)
    summary = ComprehensiveBacktestSummary(
        mode=(
            f"{backtest_settings.backtest_intrabar_interval}m intrabar replay"
            + (" [research-fast]" if backtest_settings.backtest_research_fast else "")
        ),
        starting_equity_usd=backtest_settings.backtest_starting_equity_usd,
        ending_equity_usd=ending_equity_usd,
        total_return_pct=(
            (ending_equity_usd / backtest_settings.backtest_starting_equity_usd) - 1.0
            if backtest_settings.backtest_starting_equity_usd > 0
            else 0.0
        ),
        gross_pnl_usd=simulator.realized_gross_pnl_usd,
        net_pnl_usd=simulator.realized_gross_pnl_usd - simulator.total_fees_usd,
        fees_usd=simulator.total_fees_usd,
        slippage_usd=simulator.total_slippage_usd,
        max_drawdown_pct=simulator.max_drawdown_pct,
        trade_count=len(simulator.trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(simulator.trades)) if simulator.trades else 0.0,
        profit_factor=_profit_factor(simulator.trades),
        avg_win_usd=_mean(wins),
        avg_loss_usd=_mean(losses),
        expectancy_usd=_mean([trade.net_pnl_usd for trade in simulator.trades]),
        avg_holding_minutes=_mean([trade.holding_minutes for trade in simulator.trades]),
        avg_mfe_pct=_mean([trade.mfe_pct for trade in simulator.trades]),
        avg_mae_pct=_mean([trade.mae_pct for trade in simulator.trades]),
        avg_post_exit_best_pct=_mean(
            [trade.post_exit_best_pct for trade in simulator.trades if trade.post_exit_best_pct is not None]
        ),
        avg_post_exit_worst_pct=_mean(
            [trade.post_exit_worst_pct for trade in simulator.trades if trade.post_exit_worst_pct is not None]
        ),
        avg_volatility_pct=_mean(
            [trade.volatility_pct for trade in simulator.trades if trade.volatility_pct is not None]
        ),
        take_profits=sum(1 for trade in simulator.trades if trade.exit_reason == "take_profit"),
        stop_losses=sum(1 for trade in simulator.trades if trade.exit_reason == "stop_loss"),
        forced_exits=sum(1 for trade in simulator.trades if trade.exit_reason == "end_of_backtest"),
        entry_ready_signals=simulator.entry_ready_signals,
        entries_filled=simulator.entries_filled,
        skipped_duplicate_ticker=simulator.skipped_duplicate_ticker,
        skipped_max_open_positions=simulator.skipped_max_open_positions,
        skipped_cluster_limit=simulator.skipped_cluster_limit,
        skipped_max_entries_per_rebalance=simulator.skipped_max_entries_per_rebalance,
        skipped_daily_stop_losses=simulator.skipped_daily_stop_losses,
        skipped_gross_exposure_cap=simulator.skipped_gross_exposure_cap,
        skipped_too_small=simulator.skipped_too_small,
        max_open_positions_observed=simulator.max_open_positions_observed,
        signal_summary=signal_summary,
        daily=daily,
        tickers=tickers,
    )
    return ComprehensiveBacktestResult(
        database_path=backtest_settings.sqlite_path,
        summary=summary,
        trades=simulator.trades,
        equity_curve=simulator.equity_curve,
    )


async def fetch_and_run_backtest(
    settings: Settings,
    *,
    replay_cycles: int,
    sqlite_path: str,
    intraday_regime_filter_enabled: bool | None = None,
) -> BacktestResult:
    async with aiohttp.ClientSession() as session:
        client = BybitMarketDataClient(session=session, settings=settings)
        try:
            plan = await fetch_replay_plan(client, settings, replay_cycles, settings.tracked_symbols)
        finally:
            client.close_cache()
    return await run_backtest_plan(
        settings,
        plan,
        sqlite_path=sqlite_path,
        intraday_regime_filter_enabled=intraday_regime_filter_enabled,
    )


async def fetch_and_run_comprehensive_backtest(
    settings: Settings,
    *,
    replay_cycles: int,
    sqlite_path: str,
    intraday_regime_filter_enabled: bool | None = None,
) -> ComprehensiveBacktestResult:
    async with aiohttp.ClientSession() as session:
        client = BybitMarketDataClient(session=session, settings=settings)
        try:
            plan = await fetch_minute_replay_plan(client, settings, replay_cycles)
        finally:
            client.close_cache()
    return await run_comprehensive_backtest_plan(
        settings,
        plan,
        sqlite_path=sqlite_path,
        intraday_regime_filter_enabled=intraday_regime_filter_enabled,
    )


def _build_sweep_window_end_times(
    *,
    settings: Settings,
    lookback_days: int,
    step_days: int,
    end_date: str | None = None,
    max_windows: int | None = None,
) -> list[int]:
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if step_days <= 0:
        raise ValueError("step_days must be positive")
    anchor_ms = _resolve_end_ms(settings, end_date)
    earliest_ms = anchor_ms - (lookback_days * interval_to_milliseconds("D"))
    step_ms = step_days * interval_to_milliseconds("D")
    windows: list[int] = []
    cursor = anchor_ms
    while cursor > earliest_ms:
        windows.append(cursor)
        if max_windows is not None and len(windows) >= max_windows:
            break
        cursor -= step_ms
    return windows


def _safe_db_path(base_path: str, suffix: str) -> str:
    path = Path(base_path).expanduser()
    return str(path.with_name(f"{path.stem}-{suffix}{path.suffix or '.sqlite3'}"))


def _summarize_sweep_windows(windows: list[SweepWindowComparison], skipped_windows: list[str], requested: int) -> SweepComparisonSummary:
    on_better = sum(1 for row in windows if row.winner == "filter_on")
    off_better = sum(1 for row in windows if row.winner == "filter_off")
    ties = sum(1 for row in windows if row.winner == "tie")
    return SweepComparisonSummary(
        windows_requested=requested,
        windows_completed=len(windows),
        windows_skipped=len(skipped_windows),
        filter_on_better=on_better,
        filter_off_better=off_better,
        ties=ties,
        avg_filter_on_net_pnl_usd=_mean([row.filter_on_net_pnl_usd for row in windows]),
        avg_filter_off_net_pnl_usd=_mean([row.filter_off_net_pnl_usd for row in windows]),
        avg_filter_on_return_pct=_mean([row.filter_on_return_pct for row in windows]),
        avg_filter_off_return_pct=_mean([row.filter_off_return_pct for row in windows]),
        avg_filter_on_drawdown_pct=_mean([row.filter_on_max_drawdown_pct for row in windows]),
        avg_filter_off_drawdown_pct=_mean([row.filter_off_max_drawdown_pct for row in windows]),
    )


def format_sweep_comparison(result: SweepComparisonResult) -> str:
    summary = result.summary
    lines = [
        "Sweep comparison",
        f"  windows_requested={summary.windows_requested}",
        f"  windows_completed={summary.windows_completed}",
        f"  windows_skipped={summary.windows_skipped}",
        f"  filter_on_better={summary.filter_on_better}",
        f"  filter_off_better={summary.filter_off_better}",
        f"  ties={summary.ties}",
        (
            f"  avg_net_pnl_usd: on={summary.avg_filter_on_net_pnl_usd:.2f} "
            f"off={summary.avg_filter_off_net_pnl_usd:.2f}"
            if summary.avg_filter_on_net_pnl_usd is not None and summary.avg_filter_off_net_pnl_usd is not None
            else "  avg_net_pnl_usd: n/a"
        ),
        (
            f"  avg_return_pct: on={summary.avg_filter_on_return_pct * 100:.2f} "
            f"off={summary.avg_filter_off_return_pct * 100:.2f}"
            if summary.avg_filter_on_return_pct is not None and summary.avg_filter_off_return_pct is not None
            else "  avg_return_pct: n/a"
        ),
        (
            f"  avg_max_drawdown_pct: on={summary.avg_filter_on_drawdown_pct * 100:.2f} "
            f"off={summary.avg_filter_off_drawdown_pct * 100:.2f}"
            if summary.avg_filter_on_drawdown_pct is not None and summary.avg_filter_off_drawdown_pct is not None
            else "  avg_max_drawdown_pct: n/a"
        ),
    ]
    if result.windows:
        lines.append("")
        lines.append("Windows")
        for row in result.windows:
            lines.append(
                f"  {row.window_end}: winner={row.winner} "
                f"on_net={row.filter_on_net_pnl_usd:.2f} off_net={row.filter_off_net_pnl_usd:.2f} "
                f"on_ret={row.filter_on_return_pct * 100:.2f}% off_ret={row.filter_off_return_pct * 100:.2f}% "
                f"on_dd={row.filter_on_max_drawdown_pct * 100:.2f}% off_dd={row.filter_off_max_drawdown_pct * 100:.2f}% "
                f"on_signals={row.filter_on_entry_ready_signals} off_signals={row.filter_off_entry_ready_signals}"
            )
    if result.skipped_windows:
        lines.append("")
        lines.append("Skipped windows")
        for row in result.skipped_windows:
            lines.append(f"  {row}")
    return "\n".join(lines)


def export_sweep_comparison(result: SweepComparisonResult, *, export_dir: str) -> None:
    export_path = Path(export_dir).expanduser()
    export_path.mkdir(parents=True, exist_ok=True)
    _write_csv(export_path / "sweep_windows.csv", [asdict(row) for row in result.windows])
    _write_csv(export_path / "sweep_summary.csv", [asdict(result.summary)])
    _write_csv(
        export_path / "sweep_skipped_windows.csv",
        [{"window": value} for value in result.skipped_windows],
    )


def format_variant_run_result(result: BacktestVariantRunResult) -> str:
    lines = ["Backtest variants"]
    for row in result.variants:
        profit_factor = (
            "inf"
            if row.profit_factor == float("inf")
            else f"{row.profit_factor:.2f}"
            if row.profit_factor is not None
            else "n/a"
        )
        lines.append(
            f"  {row.name}: trades={row.trade_count} wins={row.wins} losses={row.losses} "
            f"net_pnl_usd={row.net_pnl_usd:.2f} return_pct={row.total_return_pct * 100:.2f}% "
            f"max_drawdown_pct={row.max_drawdown_pct * 100:.2f}% pf={profit_factor} "
            f"entry_ready_signals={row.entry_ready_signals} filled={row.entries_filled}"
        )
    return "\n".join(lines)


def export_variant_run_result(result: BacktestVariantRunResult, *, export_dir: str) -> None:
    export_path = Path(export_dir).expanduser()
    export_path.mkdir(parents=True, exist_ok=True)
    _write_csv(export_path / "variant_summary.csv", [asdict(row) for row in result.variants])


def _select_best_variant(rows: list[BacktestVariantSummary]) -> BacktestVariantSummary:
    if not rows:
        raise ValueError("No variant rows to select from")
    return max(
        rows,
        key=lambda row: (
            row.net_pnl_usd,
            row.total_return_pct,
            -row.max_drawdown_pct,
            row.trade_count,
        ),
    )


def _rank_variants(rows: list[BacktestVariantSummary]) -> list[BacktestVariantSummary]:
    return sorted(
        rows,
        key=lambda row: (
            row.net_pnl_usd,
            row.total_return_pct,
            -row.max_drawdown_pct,
            row.trade_count,
        ),
        reverse=True,
    )


def _summarize_walk_forward(
    windows: list[WalkForwardWindowResult],
    skipped_windows: list[str],
    requested: int,
) -> WalkForwardSummary:
    return WalkForwardSummary(
        windows_requested=requested,
        windows_completed=len(windows),
        windows_skipped=len(skipped_windows),
        profitable_test_windows=sum(1 for row in windows if row.test_net_pnl_usd > 0),
        losing_test_windows=sum(1 for row in windows if row.test_net_pnl_usd < 0),
        total_test_net_pnl_usd=sum(row.test_net_pnl_usd for row in windows) if windows else None,
        avg_test_net_pnl_usd=_mean([row.test_net_pnl_usd for row in windows]),
        avg_test_return_pct=_mean([row.test_return_pct for row in windows]),
        avg_test_drawdown_pct=_mean([row.test_max_drawdown_pct for row in windows]),
    )


def format_walk_forward_result(result: WalkForwardResult) -> str:
    summary = result.summary
    lines = [
        "Walk-forward validation",
        f"  windows_requested={summary.windows_requested}",
        f"  windows_completed={summary.windows_completed}",
        f"  windows_skipped={summary.windows_skipped}",
        f"  profitable_test_windows={summary.profitable_test_windows}",
        f"  losing_test_windows={summary.losing_test_windows}",
        (
            f"  total_test_net_pnl_usd={summary.total_test_net_pnl_usd:.2f}"
            if summary.total_test_net_pnl_usd is not None
            else "  total_test_net_pnl_usd=n/a"
        ),
        (
            f"  avg_test_net_pnl_usd={summary.avg_test_net_pnl_usd:.2f}"
            if summary.avg_test_net_pnl_usd is not None
            else "  avg_test_net_pnl_usd=n/a"
        ),
        (
            f"  avg_test_return_pct={summary.avg_test_return_pct * 100:.2f}%"
            if summary.avg_test_return_pct is not None
            else "  avg_test_return_pct=n/a"
        ),
        (
            f"  avg_test_drawdown_pct={summary.avg_test_drawdown_pct * 100:.2f}%"
            if summary.avg_test_drawdown_pct is not None
            else "  avg_test_drawdown_pct=n/a"
        ),
    ]
    if result.windows:
        lines.append("")
        lines.append("Windows")
        for row in result.windows:
            lines.append(
                f"  train_end={row.train_window_end} test_end={row.test_window_end} "
                f"selected={row.selected_variant} rank={row.selected_variant_rank} "
                f"train_net={row.train_net_pnl_usd:.2f} "
                f"test_net={row.test_net_pnl_usd:.2f} "
                f"test_ret={row.test_return_pct * 100:.2f}% "
                f"test_dd={row.test_max_drawdown_pct * 100:.2f}% "
                f"test_trades={row.test_trade_count}"
            )
    if result.candidates:
        lines.append("")
        lines.append("Candidate leaderboard")
        for row in result.candidates[:20]:
            lines.append(
                f"  test_end={row.test_window_end} variant={row.variant} rank={row.rank} "
                f"selected={'yes' if row.selected else 'no'} "
                f"train_net={row.train_net_pnl_usd:.2f} "
                f"train_ret={row.train_return_pct * 100:.2f}% "
                f"train_dd={row.train_max_drawdown_pct * 100:.2f}% "
                f"train_trades={row.train_trade_count}"
            )
    if result.skipped_windows:
        lines.append("")
        lines.append("Skipped windows")
        for row in result.skipped_windows:
            lines.append(f"  {row}")
    return "\n".join(lines)


def export_walk_forward_result(result: WalkForwardResult, *, export_dir: str) -> None:
    export_path = Path(export_dir).expanduser()
    export_path.mkdir(parents=True, exist_ok=True)
    _write_csv(export_path / "walk_forward_windows.csv", [asdict(row) for row in result.windows])
    _write_csv(export_path / "walk_forward_candidates.csv", [asdict(row) for row in result.candidates])
    _write_csv(export_path / "walk_forward_summary.csv", [asdict(result.summary)])
    _write_csv(
        export_path / "walk_forward_skipped_windows.csv",
        [{"window": value} for value in result.skipped_windows],
    )


def _run_variant_sync(
    settings: Settings,
    plan: MinuteReplayPlan,
    sqlite_path: str,
    variant: BacktestVariantSpec,
) -> BacktestVariantSummary:
    variant_settings = replace(settings, **variant.overrides)
    result = asyncio.run(
        run_comprehensive_backtest_plan(
            variant_settings,
            plan,
            sqlite_path=sqlite_path,
        )
    )
    return BacktestVariantSummary(
        name=variant.name,
        database_path=result.database_path,
        trade_count=result.summary.trade_count,
        wins=result.summary.wins,
        losses=result.summary.losses,
        net_pnl_usd=result.summary.net_pnl_usd,
        total_return_pct=result.summary.total_return_pct,
        max_drawdown_pct=result.summary.max_drawdown_pct,
        profit_factor=result.summary.profit_factor,
        entry_ready_signals=result.summary.entry_ready_signals,
        entries_filled=result.summary.entries_filled,
    )


def _write_plan_snapshot(plan: MinuteReplayPlan) -> str:
    handle = tempfile.NamedTemporaryFile(
        prefix="model050426-plan-",
        suffix=".pkl",
        delete=False,
    )
    path = handle.name
    handle.close()
    with open(path, "wb") as buffer:
        pickle.dump(plan, buffer, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def _load_plan_snapshot(path: str) -> MinuteReplayPlan:
    with open(path, "rb") as buffer:
        return pickle.load(buffer)


def _run_variant_from_plan_path_sync(
    settings: Settings,
    plan_path: str,
    sqlite_path: str,
    variant: BacktestVariantSpec,
) -> BacktestVariantSummary:
    plan = _load_plan_snapshot(plan_path)
    return _run_variant_sync(settings, plan, sqlite_path, variant)


async def run_comprehensive_backtest_variants(
    settings: Settings,
    *,
    plan: MinuteReplayPlan,
    sqlite_path: str,
    variants: list[BacktestVariantSpec],
    max_workers: int | None = None,
) -> BacktestVariantRunResult:
    workers = (
        settings.backtest_variant_workers
        if max_workers is None
        else max_workers
    )
    workers = max(1, workers)
    if workers == 1 or len(variants) <= 1:
        rows = []
        for index, variant in enumerate(variants, start=1):
            variant_settings = replace(settings, **variant.overrides)
            result = await run_comprehensive_backtest_plan(
                variant_settings,
                plan,
                sqlite_path=_safe_db_path(sqlite_path, f"variant-{index:02d}"),
            )
            rows.append(
                BacktestVariantSummary(
                    name=variant.name,
                    database_path=result.database_path,
                    trade_count=result.summary.trade_count,
                    wins=result.summary.wins,
                    losses=result.summary.losses,
                    net_pnl_usd=result.summary.net_pnl_usd,
                    total_return_pct=result.summary.total_return_pct,
                    max_drawdown_pct=result.summary.max_drawdown_pct,
                    profit_factor=result.summary.profit_factor,
                    entry_ready_signals=result.summary.entry_ready_signals,
                    entries_filled=result.summary.entries_filled,
                )
            )
        return BacktestVariantRunResult(variants=rows)
    loop = asyncio.get_running_loop()
    plan_path = _write_plan_snapshot(plan)
    executor = ProcessPoolExecutor(max_workers=workers)
    try:
        tasks = [
            loop.run_in_executor(
                executor,
                _run_variant_from_plan_path_sync,
                settings,
                plan_path,
                _safe_db_path(sqlite_path, f"variant-{index:02d}"),
                variant,
            )
            for index, variant in enumerate(variants, start=1)
        ]
        rows = list(await asyncio.gather(*tasks))
    finally:
        executor.shutdown(wait=True)
        try:
            os.unlink(plan_path)
        except FileNotFoundError:
            pass
    return BacktestVariantRunResult(variants=rows)


async def run_comprehensive_backtest_sweep(
    settings: Settings,
    *,
    replay_cycles: int,
    window_end_times: list[int],
    sqlite_path: str,
    compare_intraday_regime_filter: bool = True,
) -> SweepComparisonResult:
    windows: list[SweepWindowComparison] = []
    skipped_windows: list[str] = []
    async with aiohttp.ClientSession() as session:
        client = BybitMarketDataClient(session=session, settings=settings)
        try:
            for index, window_end_ms in enumerate(window_end_times, start=1):
                label = _window_end_ms_to_label(window_end_ms)
                LOGGER.info(
                    "Sweep window %s/%s ending %s: fetching plan",
                    index,
                    len(window_end_times),
                    label,
                )
                try:
                    plan = await fetch_minute_replay_plan_for_window(
                        client,
                        settings,
                        replay_cycles,
                        replay_end_ms=window_end_ms,
                    )
                except Exception as exc:
                    LOGGER.warning("Sweep window %s skipped during fetch: %s", label, exc)
                    skipped_windows.append(f"{label} fetch_failed: {exc}")
                    continue
                try:
                    LOGGER.info(
                        "Sweep window %s/%s ending %s: running backtest",
                        index,
                        len(window_end_times),
                        label,
                    )
                    if compare_intraday_regime_filter:
                        filter_on = await run_comprehensive_backtest_plan(
                            settings,
                            plan,
                            sqlite_path=_safe_db_path(sqlite_path, f"sweep-{index:02d}-on"),
                            intraday_regime_filter_enabled=True,
                        )
                        filter_off = await run_comprehensive_backtest_plan(
                            settings,
                            plan,
                            sqlite_path=_safe_db_path(sqlite_path, f"sweep-{index:02d}-off"),
                            intraday_regime_filter_enabled=False,
                        )
                    else:
                        filter_on = await run_comprehensive_backtest_plan(
                            settings,
                            plan,
                            sqlite_path=_safe_db_path(sqlite_path, f"sweep-{index:02d}"),
                            intraday_regime_filter_enabled=(
                                settings.intraday_regime_filter_enabled
                            ),
                        )
                        filter_off = filter_on
                except Exception as exc:
                    LOGGER.warning("Sweep window %s skipped during run: %s", label, exc)
                    skipped_windows.append(f"{label} run_failed: {exc}")
                    continue
                if filter_on.summary.net_pnl_usd > filter_off.summary.net_pnl_usd:
                    winner = "filter_on"
                elif filter_off.summary.net_pnl_usd > filter_on.summary.net_pnl_usd:
                    winner = "filter_off"
                else:
                    winner = "tie"
                windows.append(
                    SweepWindowComparison(
                        window_end=label,
                        filter_on_trades=filter_on.summary.trade_count,
                        filter_off_trades=filter_off.summary.trade_count,
                        filter_on_net_pnl_usd=filter_on.summary.net_pnl_usd,
                        filter_off_net_pnl_usd=filter_off.summary.net_pnl_usd,
                        filter_on_return_pct=filter_on.summary.total_return_pct,
                        filter_off_return_pct=filter_off.summary.total_return_pct,
                        filter_on_max_drawdown_pct=filter_on.summary.max_drawdown_pct,
                        filter_off_max_drawdown_pct=filter_off.summary.max_drawdown_pct,
                        filter_on_entry_ready_signals=filter_on.summary.entry_ready_signals,
                        filter_off_entry_ready_signals=filter_off.summary.entry_ready_signals,
                        winner=winner,
                    )
                )
                LOGGER.info(
                    "Sweep window %s complete: winner=%s on_net=%.2f off_net=%.2f on_trades=%s off_trades=%s",
                    label,
                    winner,
                    filter_on.summary.net_pnl_usd,
                    filter_off.summary.net_pnl_usd,
                    filter_on.summary.trade_count,
                    filter_off.summary.trade_count,
                )
        finally:
            client.close_cache()
    summary = _summarize_sweep_windows(windows, skipped_windows, len(window_end_times))
    return SweepComparisonResult(
        summary=summary,
        windows=windows,
        skipped_windows=skipped_windows,
    )


async def run_walk_forward_validation(
    settings: Settings,
    *,
    train_days: int,
    test_days: int,
    lookback_days: int,
    sqlite_path: str,
    variants: list[BacktestVariantSpec],
    end_date: str | None = None,
    max_windows: int | None = None,
) -> WalkForwardResult:
    if train_days <= 0 or test_days <= 0 or lookback_days <= 0:
        raise ValueError("walk-forward train/test/lookback days must all be positive")
    step_days = test_days
    window_end_times = _build_sweep_window_end_times(
        settings=settings,
        lookback_days=lookback_days,
        step_days=step_days,
        end_date=end_date,
        max_windows=max_windows,
    )
    test_cycles = max((test_days * interval_to_milliseconds("D")) // settings.ticker_interval_ms, 1)
    train_cycles = max((train_days * interval_to_milliseconds("D")) // settings.ticker_interval_ms, 1)
    candidate_variants = variants or [BacktestVariantSpec(name="baseline", overrides={})]
    windows: list[WalkForwardWindowResult] = []
    candidates: list[WalkForwardCandidateResult] = []
    skipped_windows: list[str] = []
    async with aiohttp.ClientSession() as session:
        client = BybitMarketDataClient(session=session, settings=settings)
        try:
            for index, test_end_ms in enumerate(window_end_times, start=1):
                label = _window_end_ms_to_label(test_end_ms)
                train_end_ms = test_end_ms - (test_cycles * settings.ticker_interval_ms)
                if train_end_ms <= 0:
                    skipped_windows.append(f"{label} train_window_before_epoch")
                    continue
                try:
                    train_plan = await fetch_minute_replay_plan_for_window(
                        client,
                        settings,
                        train_cycles,
                        replay_end_ms=train_end_ms,
                    )
                    train_rows = await run_comprehensive_backtest_variants(
                        settings,
                        plan=train_plan,
                        sqlite_path=_safe_db_path(sqlite_path, f"walk-{index:02d}-train"),
                        variants=candidate_variants,
                        max_workers=settings.backtest_variant_workers,
                    )
                    ranked_train_rows = _rank_variants(train_rows.variants)
                    selected = ranked_train_rows[0]
                    selected_spec = next(
                        variant for variant in candidate_variants if variant.name == selected.name
                    )
                    for rank, row in enumerate(ranked_train_rows, start=1):
                        candidates.append(
                            WalkForwardCandidateResult(
                                test_window_end=label,
                                variant=row.name,
                                rank=rank,
                                selected=row.name == selected.name,
                                train_net_pnl_usd=row.net_pnl_usd,
                                train_return_pct=row.total_return_pct,
                                train_max_drawdown_pct=row.max_drawdown_pct,
                                train_trade_count=row.trade_count,
                            )
                        )
                    test_plan = await fetch_minute_replay_plan_for_window(
                        client,
                        settings,
                        test_cycles,
                        replay_end_ms=test_end_ms,
                    )
                    test_settings = replace(settings, **selected_spec.overrides)
                    test_result = await run_comprehensive_backtest_plan(
                        test_settings,
                        test_plan,
                        sqlite_path=_safe_db_path(sqlite_path, f"walk-{index:02d}-test"),
                    )
                except Exception as exc:
                    LOGGER.warning("Walk-forward window %s skipped: %s", label, exc)
                    skipped_windows.append(f"{label} failed: {exc}")
                    continue
                windows.append(
                    WalkForwardWindowResult(
                        train_window_end=_window_end_ms_to_label(train_end_ms),
                        test_window_end=label,
                        selected_variant=selected.name,
                        selected_variant_rank=1,
                        train_net_pnl_usd=selected.net_pnl_usd,
                        train_return_pct=selected.total_return_pct,
                        test_net_pnl_usd=test_result.summary.net_pnl_usd,
                        test_return_pct=test_result.summary.total_return_pct,
                        test_max_drawdown_pct=test_result.summary.max_drawdown_pct,
                        test_trade_count=test_result.summary.trade_count,
                    )
                )
        finally:
            client.close_cache()
    summary = _summarize_walk_forward(windows, skipped_windows, len(window_end_times))
    return WalkForwardResult(
        summary=summary,
        windows=windows,
        candidates=candidates,
        skipped_windows=skipped_windows,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run historical backtests through the live signal stack."
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=192,
        help="Number of 15m cycles to replay after warmup.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="backtest.sqlite3",
        help="SQLite output path for the primary backtest run.",
    )
    parser.add_argument(
        "--mode",
        choices=("intrabar", "close-proxy"),
        default="intrabar",
        help="Backtest mode. `intrabar` replays minute candles inside each 15m bar.",
    )
    parser.add_argument(
        "--disable-intraday-regime-filter",
        action="store_true",
        help="Turn off the intraday tradeability gate for the primary run.",
    )
    parser.add_argument(
        "--compare-intraday-regime-filter",
        action="store_true",
        help="Run the same backtest twice and print filter-on vs filter-off results.",
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default=None,
        help="Optional directory for CSV exports.",
    )
    parser.add_argument(
        "--research-fast",
        action="store_true",
        help="Skip signal-row persistence and report SQL during backtests to speed up parameter research.",
    )
    parser.add_argument(
        "--grid-setting",
        action="append",
        default=[],
        help="Run multiple variants against one reused replay plan. Format: KEY=value1,value2,... May be repeated.",
    )
    parser.add_argument(
        "--variant-workers",
        type=int,
        default=None,
        help="Number of worker processes for --grid-setting runs. Defaults to BACKTEST_VARIANT_WORKERS.",
    )
    parser.add_argument(
        "--stress-profile",
        action="append",
        default=[],
        help="Built-in stress profile to run as a variant axis. Options: costly, liquidity_crunch, hostile. May be repeated.",
    )
    parser.add_argument(
        "--prefetch-lookback-days",
        type=int,
        default=None,
        help="Warm the historical candle cache for the last N UTC days and exit.",
    )
    parser.add_argument(
        "--prefetch-end-date",
        type=str,
        default=None,
        help="Optional UTC anchor date for prefetch mode in YYYY-MM-DD. Defaults to now.",
    )
    parser.add_argument(
        "--sweep-lookback-days",
        type=int,
        default=None,
        help="If set, run a multi-window sweep over the last N days instead of a single window.",
    )
    parser.add_argument(
        "--sweep-step-days",
        type=int,
        default=90,
        help="Spacing in days between sweep windows.",
    )
    parser.add_argument(
        "--sweep-end-date",
        type=str,
        default=None,
        help="Optional UTC anchor date for sweep mode in YYYY-MM-DD. Defaults to now.",
    )
    parser.add_argument(
        "--sweep-max-windows",
        type=int,
        default=None,
        help="Optional hard cap on the number of sweep windows.",
    )
    parser.add_argument(
        "--walk-forward-lookback-days",
        type=int,
        default=None,
        help="If set, run walk-forward validation over the last N days instead of a single run.",
    )
    parser.add_argument(
        "--walk-forward-train-days",
        type=int,
        default=90,
        help="Training window length in days for walk-forward mode.",
    )
    parser.add_argument(
        "--walk-forward-test-days",
        type=int,
        default=30,
        help="Test window length in days for walk-forward mode.",
    )
    parser.add_argument(
        "--walk-forward-end-date",
        type=str,
        default=None,
        help="Optional UTC anchor date for walk-forward mode in YYYY-MM-DD. Defaults to now.",
    )
    parser.add_argument(
        "--walk-forward-max-windows",
        type=int,
        default=None,
        help="Optional hard cap on the number of walk-forward windows.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    if args.research_fast:
        settings = replace(settings, backtest_research_fast=True)
    db_path = str(Path(args.db).expanduser())
    grid_specs = _build_variant_specs(settings, args.grid_setting)
    stress_specs = _build_stress_variant_specs(settings, args.stress_profile)
    variant_specs = _combine_variant_specs(grid_specs, stress_specs)

    if args.prefetch_lookback_days is not None:
        async def _run_prefetch() -> PrefetchSummary:
            async with aiohttp.ClientSession() as session:
                client = BybitMarketDataClient(session=session, settings=settings)
                try:
                    return await prefetch_backtest_cache(
                        client,
                        settings,
                        lookback_days=args.prefetch_lookback_days,
                        end_date=args.prefetch_end_date,
                    )
                finally:
                    client.close_cache()

        summary = asyncio.run(_run_prefetch())
        print(format_prefetch_summary(summary))
        return

    if args.sweep_lookback_days is not None:
        if args.mode != "intrabar":
            raise ValueError("Sweep mode currently supports only --mode intrabar")
        window_end_times = _build_sweep_window_end_times(
            settings=settings,
            lookback_days=args.sweep_lookback_days,
            step_days=args.sweep_step_days,
            end_date=args.sweep_end_date,
            max_windows=args.sweep_max_windows,
        )
        result = asyncio.run(
            run_comprehensive_backtest_sweep(
                settings,
                replay_cycles=args.cycles,
                window_end_times=window_end_times,
                sqlite_path=db_path,
                compare_intraday_regime_filter=args.compare_intraday_regime_filter,
            )
        )
        if args.export_dir:
            export_sweep_comparison(result, export_dir=args.export_dir)
        print(format_sweep_comparison(result))
        return

    if args.walk_forward_lookback_days is not None:
        if args.mode != "intrabar":
            raise ValueError("Walk-forward mode currently supports only --mode intrabar")
        result = asyncio.run(
            run_walk_forward_validation(
                settings,
                train_days=args.walk_forward_train_days,
                test_days=args.walk_forward_test_days,
                lookback_days=args.walk_forward_lookback_days,
                sqlite_path=db_path,
                variants=variant_specs,
                end_date=args.walk_forward_end_date,
                max_windows=args.walk_forward_max_windows,
            )
        )
        if args.export_dir:
            export_walk_forward_result(result, export_dir=args.export_dir)
        print(format_walk_forward_result(result))
        return

    if args.mode == "close-proxy":
        if variant_specs:
            raise ValueError("--grid-setting currently supports only --mode intrabar")
        if args.compare_intraday_regime_filter:
            filter_on = asyncio.run(
                fetch_and_run_backtest(
                    settings,
                    replay_cycles=args.cycles,
                    sqlite_path=db_path,
                    intraday_regime_filter_enabled=True,
                )
            )
            filter_off_path = str(
                Path(db_path).with_name(f"{Path(db_path).stem}-filter-off.sqlite3")
            )
            filter_off = asyncio.run(
                fetch_and_run_backtest(
                    settings,
                    replay_cycles=args.cycles,
                    sqlite_path=filter_off_path,
                    intraday_regime_filter_enabled=False,
                )
            )
            print(
                "\n".join(
                    [
                        "Backtest comparison",
                        f"  filter_on_db={filter_on.database_path}",
                        f"  filter_off_db={filter_off.database_path}",
                        (
                            "  one side produced no trade summary"
                            if filter_on.summary.trade_overview is None
                            or filter_off.summary.trade_overview is None
                            else (
                                f"  trades: on={filter_on.summary.trade_overview.trade_count} "
                                f"off={filter_off.summary.trade_overview.trade_count}"
                            )
                        ),
                    ]
                )
            )
            print("\nFilter on report\n")
            print(format_report(filter_on.summary))
            print("\nFilter off report\n")
            print(format_report(filter_off.summary))
            return
        result = asyncio.run(
            fetch_and_run_backtest(
                settings,
                replay_cycles=args.cycles,
                sqlite_path=db_path,
                intraday_regime_filter_enabled=(
                    False if args.disable_intraday_regime_filter else None
                ),
            )
        )
        print(format_report(result.summary))
        return

    if args.compare_intraday_regime_filter:
        if variant_specs:
            raise ValueError("Use either --compare-intraday-regime-filter or --grid-setting, not both")
        async def _run_compare() -> tuple[ComprehensiveBacktestResult, ComprehensiveBacktestResult]:
            async with aiohttp.ClientSession() as session:
                client = BybitMarketDataClient(session=session, settings=settings)
                try:
                    plan = await fetch_minute_replay_plan(client, settings, args.cycles)
                finally:
                    client.close_cache()
            filter_on = await run_comprehensive_backtest_plan(
                settings,
                plan,
                sqlite_path=db_path,
                intraday_regime_filter_enabled=True,
            )
            filter_off_path = str(
                Path(db_path).with_name(f"{Path(db_path).stem}-filter-off.sqlite3")
            )
            filter_off = await run_comprehensive_backtest_plan(
                settings,
                plan,
                sqlite_path=filter_off_path,
                intraday_regime_filter_enabled=False,
            )
            return filter_on, filter_off

        filter_on, filter_off = asyncio.run(_run_compare())
        if args.export_dir:
            export_root = Path(args.export_dir).expanduser()
            export_comprehensive_backtest(filter_on, export_dir=str(export_root / "filter-on"))
            export_comprehensive_backtest(filter_off, export_dir=str(export_root / "filter-off"))
        print(_format_comprehensive_comparison(filter_on, filter_off))
        print("\nFilter on report\n")
        print(format_comprehensive_backtest(filter_on))
        print("\nFilter off report\n")
        print(format_comprehensive_backtest(filter_off))
        return

    if variant_specs:
        async def _run_variants() -> BacktestVariantRunResult:
            async with aiohttp.ClientSession() as session:
                client = BybitMarketDataClient(session=session, settings=settings)
                try:
                    plan = await fetch_minute_replay_plan(client, settings, args.cycles)
                finally:
                    client.close_cache()
            return await run_comprehensive_backtest_variants(
                settings,
                plan=plan,
                sqlite_path=db_path,
                variants=variant_specs,
                max_workers=args.variant_workers,
            )

        result = asyncio.run(_run_variants())
        if args.export_dir:
            export_variant_run_result(result, export_dir=args.export_dir)
        print(format_variant_run_result(result))
        return

    result = asyncio.run(
        fetch_and_run_comprehensive_backtest(
            settings,
            replay_cycles=args.cycles,
            sqlite_path=db_path,
            intraday_regime_filter_enabled=(
                False if args.disable_intraday_regime_filter else None
            ),
        )
    )
    if args.export_dir:
        export_comprehensive_backtest(result, export_dir=args.export_dir)
    print(format_comprehensive_backtest(result))


if __name__ == "__main__":
    main()
