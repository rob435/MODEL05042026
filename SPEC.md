# MODEL050426 Spec

## 1. Purpose

This document is the canonical specification for the current system.

The system is a live crypto momentum and breakout detector for a manual universe of Bybit linear USDT contracts. It is designed to:

- maintain rolling 15-minute market state
- rank the full universe cross-sectionally
- emit intrabar early-warning alerts for strengthening names
- expose a trader-facing `entry_ready` tradeable tier above broad `emerging` context
- log all evaluated rows to SQLite
- record stubbed order and position state for demo-trading evaluation
- send Telegram updates when the bot enters or exits a position
- support research-grade backtesting, walk-forward validation, and reconciliation against actual Telegram trade logs

This is not a literal copy of the original project brief. It is the current operational spec after implementation and live testing.

## 2. Scope

Included:

- Bybit public REST bootstrap for 15-minute ticker history
- Bybit public WebSocket ingestion for provisional and confirmed 15-minute candles
- Binance futures BTCDOM history as the BTC-dominance proxy input
- BTC daily regime scoring
- intrabar cross-sectional ranking
- SQLite persistence
- execution scaffolding with SQLite-backed orders and positions
- risk-based live position sizing
- simple TP/SL position handling
- real Bybit demo-account private order submission when enabled
- Telegram event alerts
- replay, smoke, benchmark, report, and universe validation tooling
- minute-aware backtesting, walk-forward validation, stress variants, and Telegram-vs-backtest reconciliation
- startup config validation, runtime manifests, health snapshots, and venue-drift monitoring

Excluded:

- shorting
- trailing stops
- fee ledger
- automatic universe discovery
- literal spot market-cap BTC dominance

## 3. Exchange And Data Inputs

### 3.1 Primary market data

Primary exchange target:

- Bybit V5 linear public market data

Used endpoints and streams:

- REST `GET /v5/market/kline`
- REST `GET /v5/market/instruments-info` for validation tooling
- WebSocket `kline.15.<symbol>` topics

### 3.2 Macro inputs

BTC regime input:

- `BTCUSDT` daily closes from Bybit

BTC dominance input:

- Binance futures `BTCDOMUSDT` history on `1h`
- if a TradingView-style symbol suffix like `.P` is configured, the client strips the suffix before hitting Binance

Important compromise:

- `BTCDOMUSDT` is treated as a practical public BTC-dominance proxy
- it is not literal spot market-cap BTC dominance

## 4. Universe

The active tradable universe is defined manually in [universe.py](universe.py).

Rules:

- all configured universe symbols are ranked
- `BTCUSDT` is also tracked for macro state, but it is not part of the ranked alt universe
- the universe is expected to be checked periodically with [universe_validator.py](universe_validator.py)

## 5. Timeframes And Windows

Core market timeframe:

- `15m`

Default rolling windows from [config.py](config.py):

- ticker state window: `288` 15-minute bars
- BTC daily lookback: `220`
- BTC volatility lookback: `30`
- BTCDOM interval: `1h`
- BTCDOM history lookback: `96`
- BTCDOM EMA period: `5`
- BTCDOM state lookback bars: `4`
- momentum lookback: `48`
- momentum skip: `4`
- curvature moving-average window: `8`
- curvature signal window: `6`
- Hurst window: `96`

## 6. Runtime Architecture

The runtime loop is asynchronous and event-driven.

Main components:

- bootstrap loader
- WebSocket supervisor
- macro refresh loop
- emerging-cycle consumer
- closed-bar consumer
- SQLite logger
- execution engine
- Telegram notifier

### 6.1 Bootstrap

On startup, the service:

- loads `STATE_WINDOW` closed 15-minute candles for every tracked symbol from Bybit
- loads `BTC_DAILY_LOOKBACK` closed daily BTC candles from Bybit
- loads `BTCDOM_HISTORY_LOOKBACK` closed 1-hour BTCDOM candles from Binance
- initializes in-memory state
- enqueues one initial confirmed cycle so the universe is ranked immediately

### 6.2 Live ingestion

The WebSocket stream listens to provisional and confirmed 15-minute candles.

For each WebSocket update:

- if `confirm=false`
  - the symbol’s provisional candle state is updated
  - an `emerging` cycle is queued
- if `confirm=true`
  - the closed candle is appended to confirmed history
  - provisional state for that symbol is cleared
  - a `confirmed` cycle is queued

### 6.3 Consumer loops

Closed-bar and emerging cycles are processed by separate consumers, but only one scoring pass runs at a time via a shared lock.

Closed-bar consumer:

- drains queued confirmed timestamps
- waits `CYCLE_SETTLE_SECONDS`
- advances confirmed history only
- updates analytics / snapshots
- does not produce tradeable or Telegram signal tiers

Emerging consumer:

- drains queued emerging timestamps
- waits `EMERGING_SETTLE_SECONDS`
- runs at most once every `EMERGING_INTERVAL_SECONDS`
- runs one full intrabar ranking pass

Execution handling:

- if execution is enabled, `entry_ready` can open a long position
- `entry_ready` promotion is additionally gated by an intraday tradeability filter built from:
  - equal-weight alt-basket drift over the recent lookback
  - equal-weight basket trend efficiency
  - positive-return breadth across the ranked universe
  - recent intrabar leadership persistence of the current leaders
- the intraday regime filter only blocks `entry_ready`; it does not suppress broader watchlist / emerging logging
- momentum ranking is residual by default:
  - `MOMENTUM_REFERENCE_MODE=absolute` uses raw symbol momentum
  - `MOMENTUM_REFERENCE_MODE=btc_relative` uses symbol/BTC relative momentum
  - `MOMENTUM_REFERENCE_MODE=basket_relative` uses symbol-vs-rest-of-basket relative momentum
  - `MOMENTUM_REFERENCE_MODE=cluster_relative` uses symbol-vs-recent-correlation-cluster relative momentum
- cluster labels are always based on recent correlation clusters
- concentration is constrained both by total open positions and by current cluster labels:
  - `MAX_OPEN_POSITIONS`
  - `MAX_POSITIONS_PER_CLUSTER`
- runtime controls also include:
  - `OPERATOR_PAUSE_NEW_ENTRIES`
  - periodic health snapshots
  - periodic venue-drift checks
- if `EXECUTION_SUBMIT_ORDERS=true`, the bot:
  - submits a real market order to Bybit demo trading
  - waits for the resulting position to appear
  - sets venue-managed TP/SL using `Set Trading Stop`
  - reconciles closed positions later from Bybit closed-PnL data
- live sizing uses risk, not fixed notional:
  - risk budget = `totalAvailableBalance * RISK_PER_TRADE_PCT`
  - stop distance = `STOP_LOSS_PCT`
  - target notional = `risk budget / stop distance`
  - quantity is rounded down to Bybit `qtyStep`
- if `EXECUTION_SUBMIT_ORDERS=false`, the bot falls back to local simulated fills and local TP/SL handling

### 6.4 Recovery

If the WebSocket fails:

- the failure is logged
- the service re-bootstraps state through REST
- it re-enqueues confirmed bootstrap cycles
- it reconnects using exponential backoff

## 7. State Model

In-memory state is maintained in [state.py](state.py).

Closed-bar ticker state:

- rolling close prices per tracked symbol
- rolling close timestamps per tracked symbol

Provisional ticker state:

- current open-candle provisional price per tracked symbol

Intrabar signal state:

- current intrabar state per universe symbol
- recent intrabar observations of rank and composite score

Global macro state:

- BTC daily closes
- BTCDOM closes
- BTC regime score
- BTCDOM state: `falling`, `neutral`, or `rising`
- BTCDOM change percentage

## 8. Indicator Definitions

All indicator math lives in [indicators.py](indicators.py).

### 8.1 Returns

- returns are log returns

Formula:

```text
r_t = log(P_t / P_{t-1})
```

### 8.2 Volatility-adjusted momentum

Momentum uses log-price change over a lagged window, divided by recent realized volatility.

Formula:

```text
momentum_raw = log(P[-skip] / P[-(lookback + skip)]) / std(returns[-(lookback + skip):-skip])
```

Constraints:

- if volatility is below `MIN_VOLATILITY`, use the floor instead
- if the required lookback is unavailable, the ticker is skipped for that cycle
- if `MOMENTUM_REFERENCE_MODE` is not `absolute`, momentum is computed on a relative-strength series instead of raw price:
  - `btc_relative`: symbol price divided by BTC price
  - `basket_relative`: symbol price divided by the equal-weight normalized basket of the rest of the universe
  - `cluster_relative`: symbol price divided by the equal-weight normalized basket of recent correlation-cluster peers

### 8.3 Curvature

Curvature is derived from smoothed returns, not from raw prices.

Process:

- compute log returns
- smooth returns with a moving average of length `CURVATURE_MA_WINDOW`
- differentiate once to get slope
- differentiate again to get curvature
- average the last `CURVATURE_SIGNAL_WINDOW` curvature values

### 8.4 Hurst

Hurst is a detrended-fluctuation-style estimate over the trailing `HURST_WINDOW` prices.

Constraints:

- values are clipped into `[0, 1]`
- invalid or short series are skipped

### 8.5 BTC regime score

BTC regime is a 0-3 macro score.

It adds one point for each of:

- BTC close above `EMA200`
- `EMA50` rising
- realized BTC daily volatility below `BTC_REALIZED_VOL_THRESHOLD`

Result:

- `0`: weakest backdrop
- `3`: strongest backdrop

### 8.6 BTCDOM state

BTCDOM is converted into a tri-state macro input.

Process:

- compute EMA over BTCDOM closes with period `BTCDOM_EMA_PERIOD`
- compare latest EMA value to the EMA value `BTCDOM_STATE_LOOKBACK_BARS` bars back
- compute percent change

Classification:

- `falling` if change `<= -0.2%`
- `neutral` if `-0.2% < change < +0.2%`
- `rising` if change `>= +0.2%`

The exact neutral band is configurable by `BTCDOM_NEUTRAL_THRESHOLD_PCT`.

## 9. Cross-Sectional Ranking

Each cycle ranks the full universe cross-sectionally.

### 9.1 Raw metrics per ticker

For each universe symbol:

- `momentum_raw`
- `curvature_raw`
- `hurst`
- current price
- cluster label
- momentum reference label

Tickers with invalid momentum, curvature, or Hurst are excluded from that cycle.

### 9.2 Z-scores

The engine computes cross-sectional z-scores for:

- raw momentum
- raw curvature

### 9.3 Clipping

Before combining the z-scores:

- `momentum_z` is clipped to `[-MOMENTUM_Z_CLIP, +MOMENTUM_Z_CLIP]`
- `curvature_z` is clipped to `[-CURVATURE_Z_CLIP, +CURVATURE_Z_CLIP]`

Defaults:

- momentum clip: `3.0`
- curvature clip: `2.5`

### 9.4 Composite score

The composite score is:

```text
composite = MOMENTUM_WEIGHT * momentum_z + CURVATURE_WEIGHT * curvature_z
```

Default weights:

- momentum: `0.85`
- curvature: `0.15`

### 9.5 Final ordering

Tickers are sorted descending by composite score.

## 10. Threshold Modulation

The engine does not use a single global score threshold.

### 10.1 Regime thresholds

Default regime floors:

- regime `3` -> `0.5`
- regime `2` -> `0.8`
- regime `1` -> `1.2`
- regime `0` -> `None`

`None` means there is no composite floor from regime alone.

### 10.2 Dominance adjustments

BTCDOM state modifies the regime floor.

Default adjustments:

- `falling` -> `-0.15`
- `neutral` -> `0.00`
- `rising` -> `+0.35`

If a regime floor exists, the effective minimum score is:

```text
effective_min_score = regime_floor + dominance_adjustment
```

Dominance is not a hard veto.

## 11. Core Qualification Filter

The current hard filter is:

- `hurst > HURST_CUTOFF`
- `composite >= effective_min_score`, when an effective minimum score exists

Notably:

- dominance no longer hard-blocks signals
- rank is handled separately by stage logic

## 12. Signal Stages

The system has two processing stages but only one tradeable signal ladder.

### 12.1 Processing stages

- `emerging`
- `confirmed`

### 12.2 Signal kinds

- `none`
- `watchlist`
- `emerging`
- `entry_ready`

## 13. Emerging Stage Specification

The emerging stage uses confirmed history plus the latest provisional candle.

### 13.1 Watchlist qualification

A ticker becomes `watchlist` if:

- it passes the core filter
- its rank is within `WATCHLIST_TOP_N`

### 13.2 Emerging promotion

A ticker becomes `emerging` if:

- it already qualifies for watchlist
- its rank is within `EMERGING_TOP_N`
- it has at least `EMERGING_MIN_OBSERVATIONS` recent intrabar observations
- rank has improved by at least `EMERGING_MIN_RANK_IMPROVEMENT`
- rank has not deteriorated across the recent observation sequence
- composite score has increased monotonically across the recent observation sequence

### 13.3 Entry-Ready Tier

`entry_ready` is the primary actionable tier.

It represents the strongest intrabar candidate:

- the setup is already strengthening
- the rank/composite shape is good enough to consider early entry before the close
- it is more selective than broad `emerging` context

The current runtime emits it directly from the intrabar signal path using stricter thresholds than broad `emerging`.

Each emitted `entry_ready` row also carries entry diagnostics describing why it passed, including:

- reference mode / reference label
- current cluster label
- intraday regime pass count
- recent rank gain
- recent composite-score gain

Default values:

- `WATCHLIST_TOP_N = 8`
- `EMERGING_TOP_N = 5`
- `ENTRY_READY_TOP_N = 4`
- `EMERGING_MIN_OBSERVATIONS = 3`
- `EMERGING_MIN_RANK_IMPROVEMENT = 2`
- `ENTRY_READY_COOLDOWN_MINUTES = 15`
- `ENTRY_READY_MIN_OBSERVATIONS = 4`
- `ENTRY_READY_MIN_RANK_IMPROVEMENT = 2`
- `ENTRY_READY_MIN_COMPOSITE_GAIN = 0.02`

Operator-facing guidance:

- `watchlist` is broad context only
- `emerging` is the developing intrabar setup
- `entry_ready` is the only tradeable candidate

### 13.4 Intrabar reset

If a ticker:

- fails the core filter, or
- falls outside `WATCHLIST_TOP_N`

then its intrabar state and intrabar observations are reset to neutral.

## 14. Confirmed Stage Specification

The confirmed stage uses confirmed history only.

Its job is deliberately narrow:

- append the newly closed bar into rolling state
- refresh analytics / portfolio snapshots
- keep the next intrabar cycle honest by advancing the closed-bar baseline

It does not emit tradeable signal kinds, Telegram alerts, or confirmation upgrades.

## 15. Research Tooling

The repo includes first-class research tooling around the live engine:

- minute-aware intrabar backtests in `backtest.py`
- reusable replay-plan grids via `--grid-setting`
- bounded worker-process parallelism via `--variant-workers`
- built-in stress variants via `--stress-profile`
- walk-forward validation with exported candidate leaderboards
- Telegram-vs-backtest reconciliation in `reconcile.py`

The intended research discipline is documented in `BACKTEST_RESEARCH_PLAN.md`.

## 16. Alerting Rules

Telegram alerting is handled by [alerting.py](alerting.py).

### 16.1 Event alerts

Event alerts are sent for:

- `emerging`
- `entry_ready`

Watchlist rows are still logged, but `watchlist` never sends Telegram alerts.

The `entry_ready` tier uses the same intrabar observation history as `watchlist` and `emerging`, but it is emitted as its own signal kind with distinct promotion and cooldown thresholds.

### 15.3 Emerging cooldowns

Emerging-stage cooldowns are keyed by `(ticker, signal_kind)`.

Defaults:

- watchlist cooldown: `30` minutes
- emerging cooldown: `60` minutes
- entry-ready cooldown: `15` minutes

An emerging-stage event only alerts when:

- the ticker newly transitions into a different intrabar state
- its cooldown for that `(ticker, signal_kind)` pair has expired

### 15.4 Success semantics

Cooldown state is updated only after a notifier send succeeds.

## 16. Logging And Persistence

SQLite persistence is handled by [database.py](database.py).

### 16.1 Signal row logging

Every evaluated ticker row is logged, even if:

- it did not alert
- it failed final rank qualification
- it was cooldown-suppressed

Stored fields include:

- timestamp
- stage
- signal kind
- ticker
- momentum z-score
- raw curvature
- Hurst
- regime score
- composite score
- alerted flag
- price
- rank
- persistence hits
- compatibility `dom_falling`
- `dom_state`
- `dom_change_pct`

## 17. Runtime Counters

The service maintains runtime counters for:

- bootstraps
- macro refreshes
- total processed cycles
- confirmed processed cycles
- emerging processed cycles
- WebSocket sessions
- WebSocket failures
- total queue drops
- confirmed queue drops
- emerging queue drops

A runtime summary is emitted on shutdown.

The runtime also persists a lightweight control plane to SQLite:

- `run_manifests`
- `runtime_health_snapshots`
- `runtime_events`

These are intended for operations and risk visibility, not alpha research.

## 18. Monitoring And Controls

Startup validation:

- invalid live config raises before the service starts
- warnings are logged and also persisted as runtime events once the DB is available

Live control plane:

- `run_manifests` stores git commit, config fingerprint, and basic execution mode
- `runtime_health_snapshots` stores queue depth, last activity timestamps, open-position count, and daily stop-loss count
- `runtime_events` stores control blocks, startup/shutdown events, and drift findings

Operator controls:

- `OPERATOR_PAUSE_NEW_ENTRIES=true` blocks fresh entries without shutting the process down
- `MAX_DAILY_STOP_LOSSES` blocks new entries after enough stop losses in the UTC day
- `MAX_OPEN_POSITIONS`, `MAX_POSITIONS_PER_CLUSTER`, and `MAX_DAILY_STOP_LOSSES` remain live guardrails

Position drift monitoring:

- when live venue mode is enabled, the runtime periodically compares local open positions against Bybit venue state
- mismatches are logged as runtime events

## 19. Rate Limits And Failure Handling

Bybit and Binance REST calls use retry with exponential backoff.

Default retry configuration:

- retries: `6`
- base backoff seconds: `2.0`

The service raises on missing or non-contiguous history because continuity is required for this strategy.

## 20. Tooling

Operator tooling shipped with the repo:

- [replay.py](replay.py): historical engine replay
- [smoke.py](smoke.py): short validation run
- [benchmark.py](benchmark.py): cycle latency benchmark
- [report.py](report.py): SQLite reporting
- [monitor.py](monitor.py): runtime manifest / health / event inspection
- [universe_validator.py](universe_validator.py): live universe check

## 21. Known Compromises

This is the honest list of current compromises:

- BTCDOM is a futures proxy, not literal BTC market-cap dominance
- the universe is manual
- intrabar processing is intentionally noisier than confirmed processing
- Hurst remains a hard filter
- the system is a signal detector only, not an execution engine

## 22. Canonical Source Of Truth

This file supersedes any informal or earlier prose descriptions of the system.

If code and this document diverge, the code wins until this document is updated.
