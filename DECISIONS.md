# Decisions

## 2026-04-03

- Chose Bybit V5 as the concrete exchange target because the spec aligns with Bybit topic semantics and closed-candle `confirm` events.
- Kept the repo flat. There is no extra package layering because the project is small and the dataflow is direct.
- Recompute cross-sectional rankings on every closed candle update instead of trying to process only the changed ticker. Ranking is inherently cross-sectional, so partial updates would be wrong.
- Add a short cycle settle delay before each processing pass so the engine ranks against a coherent candle-close wave instead of a half-updated snapshot.
- Pass candle timestamps through the queue into the confirmed signal path so close-confirmed logs and cooldown use market-event time; keep emerging alerts on wall-clock detection time because they fire before the candle is final.
- Treat BTC regime as a threshold modulator only. Regime score `0` removes the composite-score floor because the configured threshold map returns `None`; rank, Hurst, and dominance still apply.
- Use a BTC-vs-alt-basket relative-strength proxy as the dominance rotation signal. Real BTC dominance history is outside Bybit public data.
- Log every evaluated ticker to SQLite on each cycle, including cooldown-suppressed rows with `alerted=0`.
- Batch SQLite inserts per cycle and store `price`, `rank`, and `dom_falling` so the logs are useful for analysis instead of just audit trivia.
- Only update cooldown state after a notifier send actually succeeds.
- Keep a non-trivial minimum volatility floor (`1e-5`) because the raw-price momentum formula otherwise over-rewards ultra-smooth drift series.
- Add a replay harness that drives the production engine over recent historical candles rather than inventing a separate backtest-only code path.
- Add a paginated universe validator against Bybit instruments metadata because the manual symbol list will drift over time and the endpoint now exceeds the default 500-row page.
- Add a lightweight SQLite report CLI instead of expecting operators to inspect signal quality with raw SQL after every replay or live run.
- Add a one-command smoke runner so external validation is repeatable instead of depending on a manual validator -> replay -> report sequence.
- Add a benchmark CLI over the real engine path so the 100-ticker latency target can be measured repeatedly instead of asserted by guesswork.
- Add bounded-runtime support and runtime counters to `main.py` so live soak runs are repeatable and produce a shutdown summary instead of depending on manual observation.
- Add an explicit soak-run guide and production env template because deployment mistakes are more likely than code defects at this stage.
- Keep confirmed 15m history and provisional intrabar prices as separate state paths. Early watchlist logic is useful, but corrupting the confirmed history with partial candles would invalidate the close-confirmed signal.
- Use the same cross-sectional engine for both `emerging` and `confirmed` stages instead of inventing a second ranking model. The difference is the price input and alert gating, not a separate math stack.
- Do not treat every intrabar top-ranked name as a real emerging breakout. Intrabar candidates now progress through `watchlist` first and only promote to `emerging` after repeated observations show improving rank and rising composite score.
- Gate intrabar alerts on state transitions (`neutral -> watchlist`, `watchlist -> emerging`) plus separate cooldowns so the live system stays event-driven without spamming the same ticker on every Bybit kline push.
- Store both `stage` and `signal_kind` in SQLite because once the engine has intrabar state transitions, a single undifferentiated signal log becomes analytically useless.
- Treat confirmed-bar persistence as a confidence upgrade, not a hard prerequisite. A first valid confirmed breakout still matters for momentum capture, so the system upgrades to `confirmed_strong` on repeated confirmed leadership instead of suppressing early confirmed signals.
- Keep routine Telegram summaries separate from event-driven alerts. The confirmed-cycle digest exists to prove liveness and show market context; it does not change signal state or cooldown logic.
- Treat the 1-day Telegram review as evidence that the current ranking stack is too reactive for a 3-7 day momentum objective. The next tuning pass should prioritize reducing curvature dominance, replacing raw-price momentum with percentage/log momentum, and reconsidering the dominance gate as a hard blocker.
- Replace the BTC-vs-alt-basket dominance proxy with Binance futures BTCDOM history on `1h`, normalized into `falling / neutral / rising` using a `+-0.2%` neutral band.
- Modulate thresholds by dominance state instead of hard-blocking signals. Dominance is now a score adjustment, not a binary veto.
- Switch momentum to log-return normalization and reduce curvature weighting to `0.15` so the leaderboard is less sensitive to price scale and curvature noise.
- Suppress watchlist Telegram by config if needed, but keep confirmed-cycle summaries and confirmed event alerts enabled so liveness and signal context remain visible.
- Deduplicate confirmed summaries by cycle time so restarts do not resend an already-logged digest.
- Promote the current runtime behavior into a canonical written specification in `SPEC.md` so the repo no longer depends on an implied or historical spec.
- Treat the second Telegram review as evidence that the momentum/log-normalization and weaker-curvature refactor materially improved short-horizon ranking stability. Do not cut curvature further yet; the next likely tuning target is macro information content and confirmed stability behavior, not another immediate ranking rewrite.
- Add `entry_ready` as the trader-facing midpoint entry tier between `emerging` and `confirmed`, and give it explicit knobs so operators can keep it tighter than broad emerging context without turning it into a close-only filter.
- Keep repo hygiene strict: ignore local runtime artifacts, prefer clean git-based VPS deployment over `scp` of a dirty working tree, and remove dead compatibility shims instead of letting them rot.
- Add the first execution layer as a flat `execution.py` scaffold instead of pretending the detector is still purely observational. The signal engine already emits trader-facing tiers; the missing piece was persistence and policy, not another architecture rewrite.
- Enter only on `entry_ready`, not on generic `emerging`, so the first trade action stays tied to the tighter intrabar gate rather than the broad early-warning context.
- Use brutally simple position logic by default: take profit at `+2%` and stop loss at `-2%`, evaluated against the latest available price on every cycle. That is materially simpler and more testable than mixing signal-state exits into the first trading version.
- Record orders and positions in SQLite even though fills are currently simulated. If execution state is not queryable after the fact, the scaffold is operationally useless.
- Do not stop at fake fills once the user explicitly wants real demo trading. The runtime now supports signed private Bybit demo requests, market entry submission, and venue-owned TP/SL management when `EXECUTION_SUBMIT_ORDERS=true`.
- Hand TP/SL ownership to Bybit for real demo orders. Once the venue can manage the exit natively, local price-loop exits become redundant and risk fighting the exchange.
- Send Telegram updates for actual position lifecycle events, at minimum entry and TP/SL exits. Signal-only alerts are not enough once the bot starts pretending to trade.
- Keep local SQLite state even with real demo orders. The bot still needs an internal view of what it believes is open, and that state must be reconciled against Bybit closed-PnL records after venue-managed exits.
- Trust the repo-local `.env` over inherited process env for this project. Silent precedence of stale shell variables made credential debugging needlessly confusing and hid the real source of truth.
- In live demo mode, enforce "one position per ticker" against both local SQLite state and Bybit’s venue state. Local-only duplication protection is not enough if the DB ever falls behind reality.
- Drop the global `MAX_OPEN_POSITIONS` gate from the actual entry logic. It was solving the wrong problem. The meaningful safety rule here is one open position per ticker, not an arbitrary portfolio-wide entry count.
- Size live demo entries from risk, not fixed notional. With a `2%` stop and `1%` risk budget, notional is `available_balance * 0.01 / 0.02`, i.e. about `50%` of available balance per trade.

## 2026-04-06

- Treat a missing repo-root `.env` in a local checkout as a hard configuration failure for execution debugging. If the file is absent, the process may still look half-configured from inherited shell variables while keeping critical execution toggles at their default `false` values.
- When creating a local `.env` from the VPS-oriented template, override `SQLITE_PATH` to a workspace-local path instead of leaving the `/opt/...` example in place. Using the deployment example verbatim on a laptop is sloppy and makes state inspection misleading.
- Do not assume the local SQLite file explains Telegram behavior unless the logged signal kinds and timestamps actually match the chat export. In this session they did not, so the right conclusion was runtime drift, not strategy failure.
