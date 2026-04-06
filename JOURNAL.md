# Journal

## 2026-04-03

- Initialized the repository from a blank directory.
- Implemented the signal engine end to end: config, state manager, Bybit REST bootstrapper, WebSocket ingestion, indicator math, ranking, SQLite logging, Telegram alerting, async supervision, and tests.
- Resolved two spec contradictions explicitly instead of hiding them:
  - BTC `EMA200` forced the BTC daily lookback to increase from 30 to 220.
  - BTC dominance was replaced with a BTC-vs-alt-basket proxy so the system can run on Bybit market data alone.
- Raised the volatility safety floor to `1e-5` after tests showed that a near-zero denominator let smooth low-vol drifts dominate the composite score unrealistically.
- Added cycle settling, candle-gap detection, and batched SQLite writes to keep rankings coherent and per-cycle processing cheap.
- Moved cycle timestamps onto the queue/engine path and fixed cooldown so failed alert sends do not poison future alerts.
- Added a replay runner so recent Bybit history can be pushed through the production engine path for faster validation.
- Added a live universe validator against Bybit instruments metadata so dead symbols can be caught before deployment.
- Added a SQLite report CLI so replay and live logs can be inspected quickly without manual SQL.
- Ran the live universe validator and removed `FETUSDT` and `FTMUSDT` from the default universe after Bybit reported them unavailable in the current linear market.
- Added a one-command smoke runner that chains live universe validation, replay, and log reporting.
- Ran a live smoke check successfully: Bybit universe validation passed for the shipped 59-symbol tracked set, and a 4-cycle replay wrote 348 rows to `/tmp/outlier-smoke.sqlite3` with no runtime errors.
- Ran the actual service against live Bybit endpoints and confirmed the full startup path: REST bootstrap completed, BTC macro refresh completed, the WebSocket connected and subscribed, and the engine processed the bootstrap cycle cleanly.
- Added a benchmark CLI and measured the current engine path at roughly 36ms average for 100 synthetic tickers on this machine.
- Added bounded-runtime and runtime-summary support to `main.py` so the live loop can be soak-tested without manual shutdown.
- Added a deployment-oriented env template and soak-run guide so VPS rollout is scripted instead of improvised.
- Refactored the live path so the WebSocket now feeds two engine stages instead of only closed candles:
  - `emerging` intrabar ranking off provisional candle prices
  - `confirmed` ranking on 15m candle close using confirmed history only
- Added isolated provisional state in `state.py`, stage-aware SQLite logging, stage-aware Telegram messages, and separate emerging-vs-confirmed cooldown handling.
- Ran a live bounded service check after the refactor and confirmed the real process now emits repeated `emerging` cycles during the open candle while keeping the close-confirmed cycle intact, with zero websocket failures in the test window.
- Tightened the intrabar path from simple provisional top-rank polling into a real state machine: tickers now move through `WATCHLIST -> EMERGING -> CONFIRMED`, with `EMERGING` requiring recent rank improvement plus rising composite score across successive intrabar observations.
- Added `signal_kind` logging so SQLite and Telegram distinguish `watchlist`, `emerging`, `confirmed`, and `none` rows instead of hiding everything under the broader processing stage.
- Added confirmed rank persistence tracking so close-confirmed signals can upgrade to `confirmed_strong` when a ticker has held leadership across recent confirmed bars, without blocking the first confirmed breakout signal.
- Added a separate Telegram summary on every confirmed 15m cycle so operators get the top 5 and bottom 5 ranked names even when no event-driven alert transition fires.
- Reviewed one day of live Telegram output and wrote a detailed post-run report documenting severe confirmed-rank churn, top-to-bottom flips, duplicate summaries on restart, dominance acting as a hard signal gate, and curvature dominating too much of the leaderboard behavior.
- Replaced the old BTC dominance proxy with Binance BTCDOM futures history on `1h`, using `BTCDOMUSDT` as the API symbol and a `+-0.2%` neutral zone for tri-state dominance.
- Reduced curvature weight to `0.15` and switched momentum to log-return normalization so the leaderboard is less dominated by raw price scale and short-lived curvature spikes.
- Kept the old boolean dominance flag in SQLite for compatibility, while also storing `dom_state` and `dom_change_pct` for the new tri-state macro logic.
- Verified the implementation locally with `pytest` and `python3 -m py_compile`.
- Wrote `SPEC.md` as the canonical current-state specification so the repo now has an explicit contract matching the live implementation rather than an outdated implied brief.
- Reviewed the next day of Telegram output from `2026-04-04 21:00 UTC` onward and wrote `TELEGRAM_TEST_REPORT_2026-04-05.md`. The short-horizon top-to-bottom flip problem appears dramatically improved, but BTC regime remained flat at `1` all day and several symbols still spanned both top and bottom extremes over the longer window.

## 2026-04-05

- Added a real `entry_ready` signal kind between `emerging` and `confirmed`, with its own tighter intrabar thresholds for earlier tradeable candidates.
- Kept the implementation honest: the new tier still rides on the existing intrabar lifecycle, but it is now emitted directly by the signal path instead of existing only as wording.
- Added explicit `ENTRY_READY_*` env knobs so the midpoint tier can be tuned separately from broad `emerging` context without waiting for the 15m close.
- Cleaned the repo contract: aligned env templates and spec defaults with the real `entry_ready` runtime values, fixed the report section ordering, removed the dead `alerts.py` shim, and tightened deployment notes so VPS setup uses a clean git checkout instead of copying a dirty local tree.
- Added a minimal execution scaffold for Bybit demo-mode development:
  - new `execution.py` to translate `entry_ready` and `confirmed` into entry/confirm/exit actions
  - SQLite `orders` and `positions` tables with helper methods in `database.py`
  - config toggles for `EXECUTION_ENABLED`, `DEMO_MODE`, `EXECUTION_SUBMIT_ORDERS`, notional sizing, and confirmed-loss exits
- Kept the execution path brutally honest: with the current codebase, private Bybit order submission is still not implemented. The scaffold records simulated fills locally unless someone explicitly finishes the signed trade client.
- Wired the execution scaffold into `main.py` so live runs can persist simulated order/position state alongside the signal engine instead of leaving trading decisions implicit.
- Added focused execution tests and reran the suite successfully.
- Simplified position logic to the user-requested version:
  - take profit at `+2%`
  - stop loss at `-2%`
  - no confirmed-loss exit by default
- Added Telegram execution notifications for position entry and for TP/SL exits so the chat reflects actual position lifecycle events, not just signal candidates.
- Replaced the fake execution-only path with a real Bybit demo-account private client when `EXECUTION_SUBMIT_ORDERS=true`:
  - signed V5 HMAC requests against `https://api-demo.bybit.com`
  - market entry order submission
  - fill polling via position info
  - exchange-native TP/SL installation via `Set Trading Stop`
- Added venue-sync logic so locally tracked open positions can be closed from Bybit closed-PnL data after the exchange-owned TP/SL fires, with Telegram exit updates and SQLite reconciliation.
- Fixed `.env` loading so repo-local settings override stale inherited shell variables. This was the actual reason the correct demo key file kept being ignored.
- Added a hard live-entry guard so the bot skips a ticker if Bybit already reports an open venue position there, even when the local SQLite state is missing it.
- Ran a real Bybit demo-account smoke entry through the production execution path on `SOLUSDT`. The order was accepted and the bot configured venue-native TP/SL (`TP=81.16`, `SL=77.98`).
- Removed the old global open-position cap from the live entry logic. The actual guard now matches the intended rule: one open position per ticker, no duplicate local or venue entries on the same symbol.
- Switched live position sizing from fixed notional to explicit risk sizing: risk budget is now `1%` of Bybit `totalAvailableBalance`, and position notional is derived from the configured stop distance (`RISK_PER_TRADE_PCT / STOP_LOSS_PCT`).
- Started the full live bot on the demo account with the real `.env` and confirmed the production loop ran cleanly across the full universe with zero websocket failures before shutdown.

## 2026-04-06

- Investigated why local trades were not executing in `/Users/jhbvdnsbkvnsd/Desktop/MODEL050426`.
- Confirmed the repo-root `.env` was missing entirely in this checkout, so local runs were defaulting to `EXECUTION_ENABLED=false` and `EXECUTION_SUBMIT_ORDERS=false` despite shell-level Bybit credentials still being present.
- Confirmed the local `signals.sqlite3` is not the same runtime history as the exported Telegram session: the DB has no `orders`, no `positions`, and no `entry_ready` rows, while the Telegram export shows a real `SOLUSDT` demo entry plus later `entry_ready` alerts.
- Created a real repo-root `.env` for this checkout from the shipped template, wired it to the local SQLite path, and enabled Bybit demo execution so this specific workspace can place demo orders instead of silently staying detector-only.
- Left Telegram unset in the new local `.env` because no Telegram credentials were present in the current shell environment; local trading is now enabled, but Telegram delivery from this checkout still requires those two vars.
