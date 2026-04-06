# Status

## Current

- Core implementation exists.
- The live engine now supports both intrabar and close-confirmed cycles, with intrabar signals differentiated into `watchlist`, `emerging`, and `entry_ready`, and confirmed signals able to upgrade into `confirmed_strong`.
- Tests exist for indicator math, gap detection, and signal-engine behavior.
- Replay tooling exists for recent-candle validation through the production engine path.
- Universe validation tooling exists for checking the manual symbol list against Bybit’s current instruments.
- SQLite reporting tooling exists for quick inspection of replay and live signal logs.
- One-command smoke validation exists to chain universe validation, replay, and reporting.
- Benchmark tooling exists to measure cycle latency against the spec target.
- `main.py` supports bounded live runs with runtime counters for soak validation.
- Deployment docs now include a dedicated soak-run guide and production env template.
- Deployment scaffold exists for `systemd`.
- Local verification is green: `26` tests passing.
- Cycle processing now batches DB writes and waits briefly for the WebSocket close wave before scoring.
- Confirmed logs and cooldown are tied to candle event time; emerging alerts use wall-clock detection time because they fire before candle close.
- Default universe was live-validated against Bybit; `FETUSDT` and `FTMUSDT` were removed after failing validation.
- A live smoke run completed successfully against Bybit from this environment using `smoke.py --cycles 4 --strict-universe`.
- The actual service startup path was exercised against live Bybit endpoints: REST bootstrap, BTC macro refresh, WebSocket subscribe, and bootstrap-cycle processing all completed without runtime errors.
- A post-refactor live run completed successfully against Bybit with both stage paths active: one bootstrap `confirmed` cycle followed by repeated intrabar `emerging` cycles, with `websocket_failures=0`.
- The intrabar path now requires strengthening before promotion to `emerging`; it no longer equates “currently top-ranked” with “emerging breakout.”
- Confirmed-bar persistence is now tracked so repeated confirmed leadership is visible as `confirmed_strong` instead of being buried inside generic confirmed rows.
- Confirmed 15m cycles now emit a routine Telegram summary with the top and bottom ranked names so operators can distinguish "no alerts" from "no activity."
- BTCDOM macro state now comes from Binance futures history on `1h`, using a tri-state `falling / neutral / rising` dominance readout with `+-0.2%` neutral band and compatibility retention for the old boolean dominance field.
- Momentum now uses log-return normalization and curvature weight has been reduced to `0.15`, which should make the ranking less sensitive to absolute price scale and short-lived curvature spikes.
- A first 1-day live Telegram review is now documented in `TELEGRAM_TEST_REPORT_2026-04-04.md`; the main validated risk is excessive confirmed-rank churn, with multiple symbols spanning from top 5 to bottom 5 over short windows.
- `SPEC.md` now exists as the canonical source-of-truth specification for the current runtime behavior.
- A second Telegram review is now documented in `TELEGRAM_TEST_REPORT_2026-04-05.md`; the immediate top-to-bottom flip problem appears largely fixed in that window, but BTC regime stayed flat at `1` and some symbols still hit both extremes over the full day.
- The operator surface now exposes `entry_ready` as the midpoint intrabar entry tier, with explicit `ENTRY_READY_*` knobs and report/Telegram wording that distinguishes it from both broad `emerging` context and close-confirmed `confirmed` signals.
- Repo hygiene is improved: ignore rules now cover common local junk, report output sections render in the correct order, and the stale `alerts.py` compatibility shim has been removed.
- A minimal execution scaffold now exists:
  - `execution.py` can open a simulated long on `entry_ready`
  - confirmed cycles can mark that position as confirmed / confirmed-strong
  - TP at `+2%` and SL at `-2%` can close the position and persist the exit in SQLite
- SQLite now stores `orders` and `positions` in addition to raw `signals`, so demo-trading behavior can be inspected after a run instead of guessed from Telegram.
- Config now exposes explicit execution toggles for enable/disable, demo mode, simulated-vs-submitted behavior, entry notional, max open positions, TP, SL, and optional confirmed-loss exits.
- Telegram now reports actual position entry plus TP/SL exit events, not just signal candidates and confirmed summaries.
- Real Bybit demo-order submission now exists when `EXECUTION_SUBMIT_ORDERS=true`:
  - signed private V5 requests
  - market entry placement on `api-demo.bybit.com`
  - position-fill polling
  - exchange-native TP/SL via `Set Trading Stop`
  - local exit reconciliation via closed-PnL sync
- Live-entry safety now checks both local SQLite and current Bybit venue state, so the bot will not intentionally double-enter the same ticker if the venue already has an open position.
- `.env` loading now overrides stale inherited shell variables, so local credential edits actually take effect without restarting the whole environment.
- Live entry sizing is now risk-based instead of fixed-notional: `RISK_PER_TRADE_PCT` defaults to `1%` of Bybit `totalAvailableBalance`, converted into position notional through the configured stop distance.
- The practical entry rule is now one open position per ticker. `MAX_OPEN_POSITIONS` may still exist in config files, but it is not the live entry gate anymore.
- Local verification is green again: `28` tests passing.
- This checkout now has a real repo-root `.env` again, with Bybit demo execution enabled and `SQLITE_PATH` pointed at the local workspace database instead of the VPS example path.
- Telegram is still not configured in this checkout because no local `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` were available to write into `.env`.
- The local `signals.sqlite3` has been confirmed stale relative to the exported Telegram session. It is valid local state, but it is not the DB that produced the observed `entry_ready` alerts and `SOLUSDT` demo entry in chat.

## Remaining risks

- No sustained live WebSocket soak across multiple candle closes or systemd deployment run has been executed yet.
- The BTC dominance component is Binance BTCDOM futures history, not true market-cap dominance. It is still a relative-strength proxy, not literal BTC market-cap dominance.
- The manual universe may include symbols that are not currently listed on the chosen Bybit environment.
- Intrabar `watchlist` and `emerging` processing are intentionally noisier than the close-confirmed path and still need real-world observation before anyone should trust their alert quality.
- The confirmed ranking stack appears too reactive for a 3-7 day momentum capture objective. Curvature and hard dominance gating are currently the main suspects.
- The next tuning pass is now mostly about observing the Binance BTCDOM replacement in live conditions, then deciding whether the dominance adjustment is still too strong or too weak for the 3-7 day momentum objective.
- BTC regime may now be too low-information to be useful operationally if it continues to stay fixed at `1` across full live windows.
- Some symbols still span both top and bottom summary extremes over longer live windows, even though the immediate flip problem appears much better than before.
- `entry_ready` is now a real emitted intrabar signal kind between `emerging` and `confirmed`, with its own tighter rank, cooldown, observation, and composite-gain gates.
- Risk sizing still rounds quantity down to Bybit `qtyStep`, and it still assumes the configured stop distance is the true realized loss bound. A fast move through the stop can make realized risk worse than the nominal 1% target.
- TP/SL is checked only when the runtime processes a cycle. There is no separate sub-second price watcher, so a violent move can still gap through the exact configured threshold.
- Venue exit reconciliation is polling-based, not websocket-based. Telegram exit messages and SQLite close state will lag until the next engine cycle sees that Bybit has already closed the position.
- A real demo smoke entry was executed successfully on `SOLUSDT`; the venue accepted the order and TP/SL were installed. There is now a live demo position unless it has already exited on Bybit.
- Because Telegram remains unset in this local `.env`, a run from this checkout will trade on the demo venue but will not send Telegram notifications until those credentials are added.

## Next validation steps

- Run the engine on testnet or a compliant mainnet host for 5 to 7 days.
- Verify the new `orders` and `positions` tables against a multi-day demo run and check that entry, confirm, and exit transitions line up with the signal logs.
- Observe whether `1%` risk sizing with a `2%` stop produces acceptable notional sizes on the actual demo balance, or whether an additional leverage/notional cap is needed.
- Replace polling-based venue reconciliation with private websocket order/position streams if exit-notification latency becomes annoying.
- Verify that Telegram position messages are not too noisy once the bot is running intrabar for multiple days.
- Review `watchlist`, `emerging`, `entry_ready`, `confirmed`, and `confirmed_strong` logged signals for false positives, timing benefit, and missed breakouts before tuning thresholds.
- Reduce confirmed-rank churn by reweighting or clipping curvature, replacing raw-price momentum with percentage/log momentum, and testing whether dominance should modulate instead of hard-blocking.
- Use the Binance BTCDOM replacement in live conditions, then decide whether the neutral zone and dominance adjustment still need retuning for the 3-7 day momentum objective.
- Trim or refresh the manual universe against the exact Bybit market you intend to trade.
- If another live day still shows regime stuck at `1`, revisit the macro regime design before cutting curvature any further.
- If longer-horizon top/bottom span remains high, tune confirmed stability/persistence before making the ranking math more complex again.
- Check whether `entry_ready` is selective enough to serve as the primary early-action tier without making `confirmed` feel redundant or late.
- Run a short bounded local validation from this checkout to prove the new `.env` is actually being loaded and that execution no longer defaults off.
