from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from alerting import AlertPayload, SummaryEntry, SummaryPayload, TelegramNotifier
from config import Settings
from database import SignalDatabase, SignalRecord
from indicators import (
    btc_regime_score,
    clip_value,
    cross_sectional_zscores,
    curvature_signal,
    hurst_exponent,
    log_returns,
    volatility_adjusted_momentum,
)
from state import MarketState

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TickerMetrics:
    ticker: str
    current_price: float
    momentum_raw: float
    curvature_raw: float
    hurst: float
    momentum_z: float = 0.0
    curvature_z: float = 0.0
    composite_score: float = 0.0
    rank: int = 0


@dataclass(slots=True)
class RankedSignal:
    stage: str
    signal_kind: str
    ticker: str
    current_price: float
    momentum_z: float
    curvature: float
    hurst: float
    regime_score: int
    composite_score: float
    rank: int
    persistence_hits: int
    alerted: bool


@dataclass(slots=True)
class RankedTicker:
    ticker: str
    rank: int
    composite_score: float
    momentum_z: float
    curvature_z: float
    hurst: float


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        state: MarketState,
        database: SignalDatabase,
        notifier: TelegramNotifier,
    ) -> None:
        self.settings = settings
        self.state = state
        self.database = database
        self.notifier = notifier
        self.last_ranked_tickers: dict[str, list[RankedTicker]] = {
            "confirmed": [],
            "emerging": [],
        }

    def _dominance_label(self, dominance_state: int) -> str:
        if dominance_state < 0:
            return "falling"
        if dominance_state > 0:
            return "rising"
        return "neutral"

    def _confirmed_stability_bonus(self, ticker: str) -> float:
        window = max(self.settings.confirmed_persistence_window, 1)
        observations = list(self.state.confirmed_observations[ticker])
        if not observations:
            return 0.0
        qualified_hits = sum(1 for observation in observations if observation.qualified)
        persistence_ratio = qualified_hits / window
        return self.settings.confirmed_stability_weight * persistence_ratio

    def _compute_metrics(self, stage: str) -> dict[str, TickerMetrics]:
        include_provisional = stage == "emerging"
        metrics: dict[str, TickerMetrics] = {}
        for symbol in self.settings.universe:
            prices = self.state.get_prices(symbol, include_provisional=include_provisional)
            if prices.size < self.settings.state_window:
                continue
            returns = log_returns(prices)
            momentum = volatility_adjusted_momentum(
                prices=prices,
                returns=returns,
                lookback=self.settings.momentum_lookback,
                skip=self.settings.momentum_skip,
                min_volatility=self.settings.min_volatility,
            )
            curvature = curvature_signal(
                returns=returns,
                ma_window=self.settings.curvature_ma_window,
                signal_window=self.settings.curvature_signal_window,
            )
            hurst = hurst_exponent(prices[-self.settings.hurst_window :])
            if not np.isfinite(momentum) or not np.isfinite(curvature) or not np.isfinite(hurst):
                continue
            metrics[symbol] = TickerMetrics(
                ticker=symbol,
                current_price=float(prices[-1]),
                momentum_raw=momentum,
                curvature_raw=curvature,
                hurst=hurst,
            )
        if len(metrics) < 2:
            return {}

        momentum_z = cross_sectional_zscores(
            {ticker: item.momentum_raw for ticker, item in metrics.items()}
        )
        curvature_z = cross_sectional_zscores(
            {ticker: item.curvature_raw for ticker, item in metrics.items()}
        )
        for ticker, item in metrics.items():
            item.momentum_z = clip_value(
                momentum_z[ticker],
                -self.settings.momentum_z_clip,
                self.settings.momentum_z_clip,
            )
            item.curvature_z = clip_value(
                curvature_z[ticker],
                -self.settings.curvature_z_clip,
                self.settings.curvature_z_clip,
            )
            item.composite_score = (
                (self.settings.momentum_weight * item.momentum_z)
                + (self.settings.curvature_weight * item.curvature_z)
            )
            if stage == "confirmed":
                item.composite_score += self._confirmed_stability_bonus(ticker)

        ranked = sorted(metrics.values(), key=lambda item: item.composite_score, reverse=True)
        for idx, item in enumerate(ranked, start=1):
            item.rank = idx
        self.last_ranked_tickers[stage] = [
            RankedTicker(
                ticker=item.ticker,
                rank=item.rank,
                composite_score=item.composite_score,
                momentum_z=item.momentum_z,
                curvature_z=item.curvature_z,
                hurst=item.hurst,
            )
            for item in ranked
        ]
        return {item.ticker: item for item in ranked}

    def format_top_rankings(self, stage: str = "confirmed", top_n: int | None = None) -> str:
        limit = self.settings.ranking_log_top_n if top_n is None else top_n
        ranked_source = self.last_ranked_tickers
        if isinstance(ranked_source, dict):
            ranked_tickers = ranked_source.get(stage, [])
        else:
            ranked_tickers = ranked_source
        if limit <= 0 or not ranked_tickers:
            return ""
        leaders = ranked_tickers[:limit]
        return " | ".join(
            f"#{item.rank} {item.ticker} score={item.composite_score:.2f} "
            f"mom_z={item.momentum_z:.2f} cur_z={item.curvature_z:.2f} hurst={item.hurst:.2f}"
            for item in leaders
        )

    def get_ranked_tickers(self, stage: str = "confirmed") -> list[RankedTicker]:
        return list(self.last_ranked_tickers.get(stage, []))

    def build_summary_payload(
        self,
        stage: str,
        cycle_time_ms: int,
        ranked_signals: list[RankedSignal],
    ) -> SummaryPayload | None:
        ranked_tickers = self.get_ranked_tickers(stage=stage)
        if not ranked_tickers:
            return None
        top_n = max(self.settings.summary_top_n, 0)
        bottom_n = max(self.settings.summary_bottom_n, 0)
        top_rankings = [
            SummaryEntry(
                ticker=item.ticker,
                rank=item.rank,
                composite_score=item.composite_score,
                momentum_z=item.momentum_z,
                curvature_z=item.curvature_z,
                hurst=item.hurst,
            )
            for item in ranked_tickers[:top_n]
        ]
        bottom_rankings = [
            SummaryEntry(
                ticker=item.ticker,
                rank=item.rank,
                composite_score=item.composite_score,
                momentum_z=item.momentum_z,
                curvature_z=item.curvature_z,
                hurst=item.hurst,
            )
            for item in (ranked_tickers[-bottom_n:] if bottom_n else [])
        ]
        qualified_signals = [
            f"{signal.ticker} {signal.signal_kind.upper()} rank={signal.rank} "
            f"score={signal.composite_score:.2f} alerted={'yes' if signal.alerted else 'no'}"
            for signal in ranked_signals
        ]
        return SummaryPayload(
            stage=stage,
            cycle_time_ms=cycle_time_ms,
            regime_score=self.state.global_state.btc_regime_score,
            dom_state=self._dominance_label(self.state.global_state.btcdom_state),
            dom_change_pct=self.state.global_state.btcdom_change_pct,
            top_rankings=top_rankings,
            bottom_rankings=bottom_rankings,
            qualified_signals=qualified_signals,
        )

    def _compute_regime(self, include_provisional: bool = False) -> tuple[int, int]:
        regime_score = btc_regime_score(
            btc_daily_closes=self.state.global_state.btc_daily_closes,
            vol_lookback=self.settings.btc_vol_lookback,
            vol_threshold=self.settings.btc_realized_vol_threshold,
        )
        self.state.global_state.btc_regime_score = regime_score
        dominance_state = self.state.global_state.btcdom_state
        self.state.global_state.btc_dominance_series = self.state.global_state.btcdom_closes
        self.state.global_state.dom_falling = int(dominance_state < 0)
        return regime_score, dominance_state

    def _passes_core_filters(
        self,
        item: TickerMetrics,
        min_score: float | None,
    ) -> bool:
        return bool(item.hurst > self.settings.hurst_cutoff and (min_score is None or item.composite_score >= min_score))

    def _dominance_score_adjustment(self, dominance_state: int) -> float:
        return self.settings.dominance_score_adjustments.get(dominance_state, 0.0)

    def _is_strengthening_intrabar(
        self,
        ticker: str,
        min_observations: int | None = None,
        min_rank_improvement: int | None = None,
        min_composite_gain: float = 0.0,
    ) -> bool:
        observations = list(self.state.intrabar_observations[ticker])
        min_observations = (
            self.settings.emerging_min_observations
            if min_observations is None
            else min_observations
        )
        min_rank_improvement = (
            self.settings.emerging_min_rank_improvement
            if min_rank_improvement is None
            else min_rank_improvement
        )
        if len(observations) < min_observations:
            return False
        recent = observations[-min_observations:]
        rank_gain = recent[0].rank - recent[-1].rank
        if rank_gain < min_rank_improvement:
            return False
        if any(later.rank > earlier.rank for earlier, later in zip(recent, recent[1:])):
            return False
        if any(
            later.composite_score <= earlier.composite_score
            for earlier, later in zip(recent, recent[1:])
        ):
            return False
        if recent[-1].composite_score - recent[0].composite_score < min_composite_gain:
            return False
        return True

    def _classify_emerging_signal(
        self,
        ticker: str,
        item: TickerMetrics,
        observed_at_ms: int,
        min_score: float | None,
    ) -> str:
        if not self._passes_core_filters(item, min_score):
            self.state.reset_intrabar(ticker)
            return "none"
        if item.rank > self.settings.watchlist_top_n:
            self.state.reset_intrabar(ticker)
            return "none"
        self.state.record_intrabar_observation(
            symbol=ticker,
            observed_at_ms=observed_at_ms,
            rank=item.rank,
            composite_score=item.composite_score,
        )
        if (
            item.rank <= self.settings.entry_ready_top_n
            and self._is_strengthening_intrabar(
                ticker,
                min_observations=self.settings.entry_ready_min_observations,
                min_rank_improvement=self.settings.entry_ready_min_rank_improvement,
                min_composite_gain=self.settings.entry_ready_min_composite_gain,
            )
        ):
            return "entry_ready"
        if item.rank <= self.settings.emerging_top_n and self._is_strengthening_intrabar(ticker):
            return "emerging"
        return "watchlist"

    def _classify_confirmed_signal(
        self,
        ticker: str,
        item: TickerMetrics,
        observed_at_ms: int,
        min_score: float | None,
    ) -> tuple[str, int]:
        persistence_qualified = bool(
            self._passes_core_filters(item, min_score)
            and item.rank <= self.settings.confirmed_persistence_rank
        )
        self.state.record_confirmed_observation(
            symbol=ticker,
            observed_at_ms=observed_at_ms,
            rank=item.rank,
            composite_score=item.composite_score,
            qualified=persistence_qualified,
        )
        base_confirmed = bool(
            self._passes_core_filters(item, min_score)
            and item.rank <= self.settings.top_n
        )
        observations = list(self.state.confirmed_observations[ticker])
        persistence_hits = sum(1 for observation in observations if observation.qualified)
        if not base_confirmed:
            return "none", persistence_hits
        if persistence_hits >= self.settings.confirmed_persistence_min_hits:
            return "confirmed_strong", persistence_hits
        return "confirmed", persistence_hits

    async def process(
        self,
        cycle_time_ms: int | None = None,
        stage: str = "confirmed",
    ) -> list[RankedSignal]:
        include_provisional = stage == "emerging"
        metrics = self._compute_metrics(stage=stage)
        if not metrics:
            return []

        regime_score, dominance_state = self._compute_regime(include_provisional=include_provisional)
        min_score = self.settings.regime_thresholds.get(regime_score)
        dominance_adjustment = self._dominance_score_adjustment(dominance_state)
        if min_score is not None:
            min_score = min_score + dominance_adjustment
        ranked_signals: list[RankedSignal] = []
        records: list[SignalRecord] = []
        now = (
            datetime.now(timezone.utc)
            if stage == "emerging"
            else (
                datetime.fromtimestamp(cycle_time_ms / 1000, tz=timezone.utc)
                if cycle_time_ms is not None
                else datetime.now(timezone.utc)
            )
        )
        observed_at_ms = int(now.timestamp() * 1000)
        dom_falling = dominance_state < 0
        dom_state = self._dominance_label(dominance_state)
        dom_change_pct = self.state.global_state.btcdom_change_pct
        for ticker, item in sorted(metrics.items(), key=lambda pair: pair[1].rank):
            persistence_hits = 0
            if stage == "emerging":
                signal_kind = self._classify_emerging_signal(
                    ticker=ticker,
                    item=item,
                    observed_at_ms=observed_at_ms,
                    min_score=min_score,
                )
                should_signal = signal_kind != "none"
                previous_kind = self.state.intrabar_state.get(ticker, "neutral")
                alert_key = (ticker, signal_kind)
                last_alerted = self.state.last_intrabar_alerted.get(alert_key)
                cooldown = timedelta(
                    minutes=(
                        self.settings.watchlist_cooldown_minutes
                        if signal_kind == "watchlist"
                        else self.settings.entry_ready_cooldown_minutes
                        if signal_kind == "entry_ready"
                        else self.settings.emerging_cooldown_minutes
                    )
                )
                eligible_to_alert = bool(
                    should_signal
                    and signal_kind != previous_kind
                    and (last_alerted is None or now - last_alerted >= cooldown)
                )
                if signal_kind == "watchlist" and not self.settings.watchlist_telegram_enabled:
                    eligible_to_alert = False
                self.state.intrabar_state[ticker] = signal_kind if should_signal else "neutral"
            else:
                signal_kind, persistence_hits = self._classify_confirmed_signal(
                    ticker=ticker,
                    item=item,
                    observed_at_ms=observed_at_ms,
                    min_score=min_score,
                )
                should_signal = signal_kind in {"confirmed", "confirmed_strong"}
                last_alerted = self.state.last_confirmed_alerted.get((ticker, signal_kind))
                cooldown = timedelta(hours=self.settings.cooldown_hours)
                eligible_to_alert = bool(
                    should_signal
                    and (last_alerted is None or now - last_alerted >= cooldown)
                )
                if not should_signal:
                    self.state.reset_intrabar(ticker)
            alerted = False

            if not should_signal:
                if stage == "emerging":
                    self.state.intrabar_state[ticker] = "neutral"
                records.append(
                    SignalRecord(
                        timestamp=now.isoformat(),
                        stage=stage,
                        signal_kind="none",
                        ticker=ticker,
                        momentum_z=item.momentum_z,
                        curvature=item.curvature_raw,
                        hurst=item.hurst,
                        regime_score=regime_score,
                        composite_score=item.composite_score,
                        alerted=False,
                        price=item.current_price,
                        rank=item.rank,
                        persistence_hits=persistence_hits,
                        dom_falling=dom_falling,
                        dom_state=dom_state,
                        dom_change_pct=dom_change_pct,
                    )
                )
                continue

            signal = RankedSignal(
                stage=stage,
                signal_kind=signal_kind,
                ticker=ticker,
                current_price=item.current_price,
                momentum_z=item.momentum_z,
                curvature=item.curvature_raw,
                hurst=item.hurst,
                regime_score=regime_score,
                composite_score=item.composite_score,
                rank=item.rank,
                persistence_hits=persistence_hits,
                alerted=False,
            )
            ranked_signals.append(signal)
            if eligible_to_alert:
                try:
                    alerted = await self.notifier.send(
                        AlertPayload(
                            stage=stage,
                            signal_kind=signal_kind,
                            ticker=ticker,
                            composite_score=item.composite_score,
                            momentum_z=item.momentum_z,
                            curvature=item.curvature_raw,
                            hurst=item.hurst,
                            current_price=item.current_price,
                            regime_score=regime_score,
                            rank=item.rank,
                            persistence_hits=persistence_hits,
                            persistence_window=self.settings.confirmed_persistence_window if stage == "confirmed" else None,
                            persistence_min_hits=self.settings.confirmed_persistence_min_hits if stage == "confirmed" else None,
                        )
                    )
                except Exception:
                    LOGGER.exception("Alert delivery failed for %s", ticker)
                    alerted = False
                signal.alerted = alerted
                if alerted:
                    if stage == "emerging":
                        self.state.last_intrabar_alerted[(ticker, signal_kind)] = now
                    else:
                        self.state.last_confirmed_alerted[(ticker, signal_kind)] = now
                        self.state.last_alerted[ticker] = now
            records.append(
                SignalRecord(
                    timestamp=now.isoformat(),
                    stage=stage,
                    signal_kind=signal_kind,
                    ticker=ticker,
                    momentum_z=item.momentum_z,
                    curvature=item.curvature_raw,
                    hurst=item.hurst,
                    regime_score=regime_score,
                    composite_score=item.composite_score,
                    alerted=alerted,
                    price=item.current_price,
                    rank=item.rank,
                    persistence_hits=persistence_hits,
                    dom_falling=dom_falling,
                    dom_state=dom_state,
                    dom_change_pct=dom_change_pct,
                )
            )
        await self.database.log_signals(records)
        return ranked_signals
