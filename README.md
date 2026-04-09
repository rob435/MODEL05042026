# MODEL050426

Real-time crypto breakout detector built around Bybit V5 candle streams, volatility-adjusted momentum, return curvature, BTC regime scoring, and cross-sectional ranking.

## Canonical Spec

The current source-of-truth system specification is [SPEC.md](SPEC.md).
Use that document for the actual runtime contract. This README is now an operator overview.

## What it does

- Bootstraps 15m history for a manual universe of midcap USDT contracts plus `BTCUSDT`.
- Subscribes to Bybit public `kline.15` WebSocket updates and processes both intrabar updates and confirmed candle closes.
- Recomputes cross-sectional scores on two stages:
  - `emerging`: intrabar ranking off provisional current-candle prices
  - `confirmed`: full 15m close ranking off confirmed history only
- Differentiates intrabar states instead of treating every top-ranked provisional move the same:
  - `WATCHLIST`: ticker enters the broad intrabar leaders
  - `EMERGING`: ticker is strengthening across recent intrabar observations
  - `ENTRY_READY`: trader-facing midpoint entry tier for the strongest intrabar candidate before the close
  - `CONFIRMED`: ticker qualifies on the closed 15m candle
  - `CONFIRMED_STRONG`: ticker qualifies on the closed 15m candle and has also held confirmed leadership across recent confirmed bars
- Uses a short settle delay before each processing cycle so ranking runs on a mostly complete 15m snapshot instead of the first symbol that arrives.
- Throttles `emerging` processing with a minimum interval so intrabar updates stay event-driven without recalculating on every raw WebSocket packet.
- Carries candle timestamps through the queue path for `confirmed` cycles; `emerging` rows use wall-clock detection time because intrabar alerts happen before the candle is final.
- Uses BTC daily regime as a threshold modulator, not a hard block.
- Uses Binance BTCDOM futures history as the dominance rotation signal, normalized into `falling / neutral / rising` with a `+-0.2%` neutral zone.
- Logs every evaluated ticker on each cycle to SQLite and optionally sends Telegram alerts with cooldown control.
- Sends a separate Telegram summary on every confirmed 15m cycle with the top and bottom ranked names, so you can see what the engine is seeing even when no fresh signal transition fires.
- Logs the top-ranked names each cycle so you can see the leaders even when no symbol passes the final alert filters.
- Keeps provisional intrabar prices isolated from the confirmed 15m history so early alerts do not contaminate the close-confirmed signal path.
- Executes the current signal stack as a short-only strategy: sell to open, buy to cover, TP at `3%` down, SL at `2%` up.
- Persists analytics for closed trades, open-position marks, post-exit follow-through, and portfolio snapshots so TP/SL and capacity can be studied from real runtime data instead of screenshots.

## Current Position

- `EMA200` requires more than 30 daily BTC closes, so the runtime stores 220 BTC daily closes.
- BTCDOM is implemented through Binance futures history as a practical public proxy, not literal spot market-cap dominance.

## Next Tuning Pass

The 1-day Telegram review showed that the engine is still too reactive for a 3-7 day momentum objective. The current tuning priorities are:

- Observe the Binance BTCDOM replacement in live conditions and decide whether the dominance adjustment is still too strong or too weak.
- Keep curvature capped at `0.15` so it behaves like a timing aid, not a rank driver.
- Keep log-momentum normalization so cross-sectional comparisons are less distorted by nominal price level.
- Keep confirmed ranking slightly persistence-biased so one-bar spikes stop dominating the 15m summary.
- Disable watchlist Telegram by config if the feed becomes too noisy; keep confirmed summaries and stronger event alerts on Telegram.
- Keep duplicate confirmed-summary suppression keyed by cycle time so restarts do not resend the same digest.

## Layout

- `config.py`: environment-driven settings
- `universe.py`: manual universe list
- `state.py`: in-memory rolling state
- `indicators.py`: pure numeric routines
- `exchange.py`: Bybit REST bootstrap + WebSocket ingestion
- `signal_engine.py`: ranking, signal generation, cooldown, logging, alert dispatch
- `database.py`: SQLite persistence
- `alerting.py`: Telegram output
- `main.py`: process wiring and supervision

## Run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp deploy/production.env.example .env
python main.py
```

## Test

```bash
pytest
```

## Replay

Use the replay harness to drive the production engine over recent Bybit history without waiting days for live validation:

```bash
python replay.py --cycles 96 --db replay-signals.sqlite3
```

This warms state with the first `STATE_WINDOW` candles, then replays the remaining cycles in market-event time.

## Universe Validation

Validate the configured universe against Bybit’s live instruments list before a deployment:

```bash
python universe_validator.py
```

This uses Bybit `GET /v5/market/instruments-info` with pagination because `linear` instruments exceed the default 500-row response.

## Reporting

Summarize a replay or live SQLite log without hand-writing SQL:

```bash
python report.py --db replay-signals.sqlite3 --top 10
```

The report now includes a stage and signal-kind breakdown so you can see how much of the log came from `watchlist`, `emerging`, `entry_ready`, `confirmed`, and `confirmed_strong` activity.

It also summarizes:

- daily trade results with wins, losses, stop-loss count, take-profit count, and net PnL
- closed-trade analytics such as holding time, MFE, MAE, post-exit follow-through, and volatility
- portfolio snapshots with open-position count, gross notional, balance-based capacity estimates, and daily stop-loss totals

Export CSVs for VPS retrieval:

```bash
python report.py --db signals.sqlite3 --export-dir exports
```

## VPS Sync

Use the VPS as the source of truth and pull analytics exports back to this Mac on a schedule.

The intended flow is:

1. A local script at `deploy/sync_exports.sh` creates a safe SQLite backup on the VPS at `root@204.168.202.167`.
2. The script pulls the backup down to the Mac and also captures the latest `systemctl status` and `journalctl` tail.
3. `report.py --export-dir ...` then regenerates local CSVs for analysis from the pulled SQLite copy.

The shipped launchd template is [deploy/model050426-export-sync.plist](deploy/model050426-export-sync.plist). It runs once per day at `09:00` local time and writes logs to `~/Library/Logs/model050426-export-sync.log` and `~/Library/Logs/model050426-export-sync.err`.

Use the installer on macOS instead of pointing launchd at the repo checkout directly. A repo living under `~/Desktop` is TCC-protected, and launchd can fail with `Operation not permitted`.

Run one sync manually:

```bash
./deploy/sync_exports.sh
```

Default local output paths:

- SQLite backup: `~/MODEL050426-sync/db/signals.sqlite3`
- CSV exports: `~/MODEL050426-sync/exports/latest/`
- rendered report text: `~/MODEL050426-sync/reports/latest.txt`
- remote service status: `~/MODEL050426-sync/logs/vps-status.txt`
- remote journal tail: `~/MODEL050426-sync/logs/journal-tail.log`

Install the launch agent and copy the sync assets to `~/Library/Application Support/MODEL050426-sync/`:

```bash
./deploy/install_export_sync.sh
```

If you want a different daily time, set it before install:

```bash
MODEL050426_SYNC_HOUR=6 MODEL050426_SYNC_MINUTE=30 ./deploy/install_export_sync.sh
```

## Smoke Run

Run universe validation plus a short live-data replay in one command:

```bash
python smoke.py --cycles 4 --db smoke-replay.sqlite3 --strict-universe
```

## Benchmark

Measure signal-engine cycle latency against synthetic data:

```bash
python benchmark.py --tickers 100 --cycles 20
```

On this machine, the current engine path measured about `36ms` average for `100` synthetic tickers including SQLite writes, which is inside the `<100ms` target.

## Bounded Live Run

Run the actual service for a fixed time window with Telegram disabled:

```bash
python main.py --run-seconds 300 --disable-telegram
```

This is the honest soak-test path for startup, WebSocket stability, macro refresh, queue handling, and clean shutdown. The process logs a runtime summary on exit.

## Deployment

`deploy/model050426.service` is the shipped `systemd` unit for a checkout at `/opt/MODEL05042026`.

For the first VPS validation run, use [SOAK_RUN.md](deploy/SOAK_RUN.md) and start from [production.env.example](deploy/production.env.example).

## Operational notes

- Bybit REST `GET /v5/market/kline` returns candles in reverse chronological order and includes the still-open candle; the bootstrapper normalizes to ascending closed candles only.
- Bootstrap is the heaviest REST phase. Repeated local restarts can hit Bybit `retCode=10006`; the client now backs off and retries automatically.
- The WebSocket connection will drop eventually. The supervisor reconnects, reboots state through REST, and resumes the stream.
- Intrabar cycles will fire repeatedly during an open 15m candle whenever Bybit pushes provisional kline updates. That is intentional; alert sends are transition-gated into `WATCHLIST` and `EMERGING` states instead of firing on every pass.
- `EMERGING` requires strengthening, not just presence. The intrabar state machine looks for repeated watchlist observations with improving rank and rising composite score before promoting a ticker from `WATCHLIST` to `EMERGING`.
- `ENTRY_READY` is the midpoint signal kind between `EMERGING` and `CONFIRMED`. It is the trader-oriented label for the strongest intrabar candidate and should be read as "this is close enough to trade, but still intrabar."
- `ENTRY_READY` has explicit tuning knobs in the env templates: `ENTRY_READY_TOP_N`, `ENTRY_READY_COOLDOWN_MINUTES`, `ENTRY_READY_MIN_OBSERVATIONS`, `ENTRY_READY_MIN_RANK_IMPROVEMENT`, and `ENTRY_READY_MIN_COMPOSITE_GAIN`. Use those to keep the midpoint tighter than `EMERGING` without turning it into another close-based filter.
- Execution is currently short-only. The strategy direction changed, but the ranking engine did not; the current run is explicitly testing the inverted bias rather than claiming a new alpha model.
- `MAX_OPEN_POSITIONS` is back as a blunt portfolio safety net. It caps the total number of simultaneously open positions, while `MAX_ENTRIES_PER_REBALANCE` separately caps how many new names one rebalance pass may add.
- `MAX_ENTRIES_PER_REBALANCE` is now the clean way to cap how many fresh positions one `emerging` rebalance pass may open. It does not change ranking; it just limits how many top-ranked `entry_ready` names are allowed through in that batch.
- `CONFIRMED_STRONG` is not a hard gate. The first valid confirmed bar can still alert as `CONFIRMED`; the signal upgrades to `CONFIRMED_STRONG` when recent confirmed-bar history also supports it.
- If a candle gap is detected mid-stream, the supervisor falls back to a fresh REST bootstrap instead of pretending state is intact.
- Cooldown only advances after a successful alert send; a failed Telegram request does not silently suppress the next valid signal.
- `emerging` and `confirmed` alerts use separate cooldown state, so an early watchlist alert does not block the later close-confirmed alert for the same ticker.
- Confirmed-cycle summaries are separate from event alerts. They are a routine operator digest, not a signal trigger, and default to the top 5 plus bottom 5 names.
- Analytics env knobs now include `MAX_DAILY_STOP_LOSSES`, `ANALYTICS_POST_EXIT_BARS`, `ANALYTICS_LOG_POSITION_MARKS`, and `ANALYTICS_LOG_PORTFOLIO_SNAPSHOTS`.
- Execution throttles now include `MAX_ENTRIES_PER_REBALANCE` for per-batch entry control and `MAX_DAILY_STOP_LOSSES` for the UTC-day kill switch.
- For VPS analysis, prefer pulling the SQLite backup to the Mac and regenerating CSVs locally rather than grepping raw logs. Example:

```bash
./deploy/sync_exports.sh
python report.py --db ~/MODEL050426-sync/db/signals.sqlite3 --export-dir ~/MODEL050426-sync/exports/latest
```

- Raw `signals.csv` export is now opt-in because it gets huge fast. If you want it anyway, add `--include-signals-export`.
- The runtime uses the `certifi` CA bundle for outbound TLS, which avoids common macOS Python certificate-store breakage.
- If you run from a US-routed host, Bybit mainnet access may be blocked. Use a compliant region or testnet/base URL override.

## Docs used

- Bybit Get Kline: https://bybit-exchange.github.io/docs/v5/market/kline
- Bybit WebSocket Connect: https://bybit-exchange.github.io/docs/v5/ws/connect
