from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from config import Settings
from indicators import dominance_state


class CandleGapError(RuntimeError):
    pass


@dataclass(slots=True)
class GlobalState:
    btc_daily_closes: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    btcdom_closes: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    btc_regime_score: int = 0
    btc_dominance_series: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    dom_falling: int = 1
    btcdom_state: int = 0
    btcdom_change_pct: float = 0.0


@dataclass(slots=True)
class ProvisionalCandle:
    start_time_ms: int
    close_price: float


@dataclass(slots=True)
class IntrabarObservation:
    observed_at_ms: int
    rank: int
    composite_score: float


@dataclass(slots=True)
class MarketState:
    settings: Settings
    price_state: dict[str, deque] = field(init=False)
    close_times_ms: dict[str, deque] = field(init=False)
    last_alerted: dict[str, datetime] = field(default_factory=dict)
    last_intrabar_alerted: dict[tuple[str, str], datetime] = field(default_factory=dict)
    global_state: GlobalState = field(default_factory=GlobalState)
    provisional_state: dict[str, ProvisionalCandle | None] = field(init=False)
    intrabar_state: dict[str, str] = field(init=False)
    intrabar_observations: dict[str, deque] = field(init=False)

    def __post_init__(self) -> None:
        self.price_state = {
            symbol: deque(maxlen=self.settings.state_window)
            for symbol in self.settings.tracked_symbols
        }
        self.close_times_ms = {
            symbol: deque(maxlen=self.settings.state_window)
            for symbol in self.settings.tracked_symbols
        }
        self.provisional_state = {
            symbol: None
            for symbol in self.settings.tracked_symbols
        }
        self.intrabar_state = {
            symbol: "neutral"
            for symbol in self.settings.universe
        }
        observation_window = max(self.settings.emerging_min_observations + 2, 6)
        self.intrabar_observations = {
            symbol: deque(maxlen=observation_window)
            for symbol in self.settings.universe
        }

    def replace_history(self, symbol: str, candles: list[tuple[int, float]]) -> None:
        prices = self.price_state[symbol]
        times = self.close_times_ms[symbol]
        prices.clear()
        times.clear()
        self.provisional_state[symbol] = None
        self.reset_intrabar(symbol)
        for close_time_ms, close_price in candles:
            times.append(close_time_ms)
            prices.append(close_price)

    def replace_btcdom_history(self, candles: list[tuple[int, float]]) -> None:
        self.global_state.btcdom_closes = np.asarray([close for _, close in candles], dtype=float)
        self.global_state.btc_dominance_series = self.global_state.btcdom_closes
        self.refresh_btcdom_state()

    def append_close(self, symbol: str, close_time_ms: int, close_price: float) -> bool:
        times = self.close_times_ms[symbol]
        prices = self.price_state[symbol]
        if times and close_time_ms <= times[-1]:
            return False
        if times and close_time_ms - times[-1] > self.settings.ticker_interval_ms:
            raise CandleGapError(
                f"Gap detected for {symbol}: expected {times[-1] + self.settings.ticker_interval_ms}, got {close_time_ms}"
            )
        times.append(close_time_ms)
        prices.append(close_price)
        provisional = self.provisional_state[symbol]
        if provisional is not None and provisional.start_time_ms <= close_time_ms:
            self.provisional_state[symbol] = None
        self.reset_intrabar(symbol)
        return True

    def update_provisional(self, symbol: str, close_time_ms: int, close_price: float) -> bool:
        times = self.close_times_ms[symbol]
        if not times or close_time_ms <= times[-1]:
            return False
        expected_next = times[-1] + self.settings.ticker_interval_ms
        provisional = self.provisional_state[symbol]
        if provisional is not None:
            if close_time_ms < provisional.start_time_ms:
                return False
            if close_time_ms == provisional.start_time_ms:
                if provisional.close_price == close_price:
                    return False
                provisional.close_price = close_price
                return True
            raise CandleGapError(
                f"Provisional candle advanced unexpectedly for {symbol}: "
                f"{provisional.start_time_ms} -> {close_time_ms}"
            )
        if close_time_ms > expected_next:
            raise CandleGapError(
                f"Gap detected for {symbol}: expected provisional {expected_next}, got {close_time_ms}"
            )
        if close_time_ms != expected_next:
            return False

        self.provisional_state[symbol] = ProvisionalCandle(
            start_time_ms=close_time_ms,
            close_price=close_price,
        )
        return True

    def get_prices(self, symbol: str, include_provisional: bool = False) -> np.ndarray:
        prices = np.asarray(self.price_state[symbol], dtype=float)
        if not include_provisional:
            return prices
        provisional = self.provisional_state[symbol]
        if provisional is None:
            return prices
        times = self.close_times_ms[symbol]
        if times and provisional.start_time_ms <= times[-1]:
            return prices
        return np.append(prices, provisional.close_price)

    def reset_intrabar(self, symbol: str) -> None:
        if symbol not in self.intrabar_state:
            return
        self.intrabar_state[symbol] = "neutral"
        self.intrabar_observations[symbol].clear()

    def record_intrabar_observation(
        self,
        symbol: str,
        observed_at_ms: int,
        rank: int,
        composite_score: float,
    ) -> None:
        if symbol not in self.intrabar_observations:
            return
        observations = self.intrabar_observations[symbol]
        if observations and observations[-1].observed_at_ms == observed_at_ms:
            observations[-1] = IntrabarObservation(
                observed_at_ms=observed_at_ms,
                rank=rank,
                composite_score=composite_score,
            )
            return
        observations.append(
            IntrabarObservation(
                observed_at_ms=observed_at_ms,
                rank=rank,
                composite_score=composite_score,
            )
        )

    def refresh_btcdom_state(self) -> None:
        closes = self.global_state.btcdom_closes
        state, change_pct = dominance_state(
            closes,
            ema_period=self.settings.btcdom_ema_period,
            lag=self.settings.btcdom_state_lookback_bars,
            neutral_threshold_pct=self.settings.btcdom_neutral_threshold_pct,
        )
        self.global_state.btcdom_state = state
        self.global_state.btcdom_change_pct = change_pct
        self.global_state.dom_falling = int(state < 0)


class StateManager(MarketState):
    def replace_ticker_history(self, ticker: str, candles: list[tuple[int, float]]) -> None:
        self.replace_history(ticker, candles)

    def replace_btc_daily_history(self, candles: list[tuple[int, float]]) -> None:
        self.global_state.btc_daily_closes = np.asarray([close for _, close in candles], dtype=float)
