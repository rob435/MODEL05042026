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

## 2026-04-09

- Keep the ranking and signal-generation logic unchanged for now and invert only the execution layer. This makes the current system an explicit contrarian test instead of pretending it has become a different alpha model.
- Use asymmetric short exits of `TP=3%` and `SL=2%` for this inversion pass. If the short bias still fails under those terms, the signal is probably weak rather than merely directionally inverted.
- Put research data in SQLite, not just the app log. Journald is for runtime/debugging; SQLite is the source of truth for daily stats, trade analytics, position marks, and portfolio snapshots.
- Track post-exit follow-through directly in the runtime so TP/SL tuning is based on what trades did after closure, not just whether they closed green or red.
- Use a UTC-day stop-loss breaker (`MAX_DAILY_STOP_LOSSES`) instead of ad hoc manual shutdown when the bot is clearly trading poorly intraday.
- Use CSV export from `report.py` as the VPS retrieval path. Pull flat files with `rsync`; do not make future analysis depend on SSHing in and hand-querying SQLite every time.
- Keep the export-sync automation pull-based from the Mac side. The VPS should remain the source of truth, and the local machine should schedule the backup fetch and analysis pass instead of relying on the server to push files outward.
- Add a separate `MAX_ENTRIES_PER_REBALANCE` gate instead of reviving the dead portfolio-wide open-position cap. The useful control here is "how many fresh names may one emerging batch open," not "pretend portfolio state alone solves correlation."
- Remove dead config surface aggressively when it no longer changes runtime behavior. In this pass that meant dropping `MAX_OPEN_POSITIONS` and `ANALYTICS_EXPORT_DIR` instead of keeping misleading env knobs around for compatibility theater.
- Restore `MAX_OPEN_POSITIONS` once the user explicitly wants it back as a hard safety net. It is still a blunt tool, but that is fine if it is treated honestly as a kill-switch style portfolio cap rather than alpha logic.
- Organize the production env template by impact, not by historical accident. Telegram, execution/risk, and high-impact signal knobs should be visually obvious because those are the settings people actually reach for during live tuning.
- Split Telegram control into signal alerts vs execution events. People often want fills and exits without candidate spam; that requires a dedicated signal-alert master switch, not more overloaded summary toggles.

## 2026-04-10

- Align execution direction with the momentum ranking instead of keeping the short inversion. If the signal is being tested as momentum again, the runtime should buy strength rather than forcing a contrarian expression.
- Use symmetric `TP=2%` and `SL=2%` for the restored long path. It is simpler, consistent with the original momentum framing, and easier to reason about than carrying forward the short experiment's asymmetric `3% / 2%` exits.
- Add the real no-trade logic at the intraday layer, not by making BTC macro thresholds more clever. The practical failure mode here is session type mismatch, so `entry_ready` now requires enough breadth, drift, trend efficiency, and leader persistence before the bot is allowed to treat the day as momentum-tradeable.
- Build the first actual backtest around the live engines, not around a separate spreadsheet model. Even a close-proxy intrabar approximation is more honest than inventing another signal calculator just for historical testing.
- Treat a zero-trade historical run as information, not as success theater. If the current `entry_ready` logic does not fire under a close-proxy replay, then the harness has exposed a real limitation of the approximation and likely a need for minute-level intrabar reconstruction.
- The serious backtest should be minute-aware by default. `entry_ready` is an intrabar concept, so a close-proxy-only harness is not good enough to judge the regime filter or TP/SL quality honestly.
- Use historical minute `high` / `low` for TP/SL evaluation, but process exits before new entries inside each minute. Letting a just-opened trade benefit or suffer from the same candle extremes that happened before the entry close is backtest garbage.
- Keep the simulator portfolio-aware and deliberately conservative:
  - equity-based sizing instead of fixed toy notional
  - modeled fees and slippage
  - gross exposure cap
  - same-minute TP+SL conflicts resolved pessimistically
- Parallelize minute-history fetches. Serializing dozens of symbol-range pulls was wasted runtime, not useful realism.
- Judge the intraday regime filter on longer windows, not tiny samples. On a `32`-bar window the results were identical on and off; on `96` bars the filter cut signals materially and improved net PnL. That is still not proof of robustness, but it is the first nontrivial evidence that the gate may actually help.
- Build sweep mode into the backtest itself instead of doing ad hoc scripts. Repeated historical windows are how this strategy should be judged, because one recent day can flatter or smear the filter too easily.
- Treat missing older candles as a universe-availability problem, not a code failure. If a current ticker did not exist in `2024`, the correct behavior is to skip that full-universe window and say so explicitly.
- Based on the sampled 1-year and 2-year sweeps, the intraday gate should be treated as a selectivity / drawdown-control layer, not as a guaranteed alpha enhancer. It improved average return and drawdown on the sampled windows, but it still lost on some strong momentum sessions by being too restrictive.
- Keep universe cleanup explicit and honest. If a user wants to retain a symbol like `RENDERUSDT` despite missing older history, accept that choice and treat the consequence correctly: older full-universe sweeps must either skip that window or move the start date forward.

## 2026-04-11

- Put historical candle caching inside the existing exchange client instead of creating a separate backtest-only data loader. The backtest should reuse the same fetch surface and simply become cheaper, not grow a second inconsistent source of truth.
- Use SQLite for the backtest candle cache. One year of `1m` candles across the full universe is too large and too repetitive for JSON/CSV junk files, and SQLite gives deduped keyed storage plus cheap reuse across runs.
- Make cache warming a first-class `backtest.py` command (`--prefetch-lookback-days`) instead of a one-off script. If the workflow is not discoverable from the main research entrypoint, it will rot.
- Treat a fully warmed year of `1m` universe data as a storage tradeoff, not a free win. It saves network time on repeated research, but it is large on disk and does not fix the CPU cost of the minute-by-minute replay loop.
- Optimize the actual replay hotspot before shaving smaller edges. The profile made this obvious: Hurst detrending was eating the run, so replacing repeated `np.polyfit` work with an equivalent vectorized linear-detrend path was the highest-value safe optimization.
- Add a separate `research-fast` mode instead of quietly degrading the default backtest. Full mode remains the audited path; fast mode is an explicit choice for broad parameter sweeps where signal-row persistence and SQL summaries are just bookkeeping drag.
- In fast mode, only skip work that does not affect trade or equity correctness:
  - no per-pass signal-row persistence
  - no post-run signal-summary SQL scan
  - no intraday regime scan when the gate is not participating in decisions
  The execution and equity path stays intact.
- Make plan reuse a first-class backtest workflow. Parameter sweeps should fetch/build the historical replay input once, then run multiple settings over that same immutable `MinuteReplayPlan`. Rebuilding the same plan for every variant is just wasted setup time.
- Use separate worker processes, not threads, for variant parallelism. The replay is CPU-heavy, each variant already has naturally isolated state/DB outputs, and thread-level shared-state complexity is not worth the risk for a real-money research path.
- Keep process parallelism portable by snapshotting the prebuilt replay plan to disk once and loading it in workers. On Linux, a `fork`-based model could be even faster, but the file-snapshot approach is safer across local macOS development and avoids hidden event-loop/resource inheritance bugs.
- Keep the serial path async-safe. The first benchmark exposed a real bug where the serial variant path called `asyncio.run()` from inside an active event loop; that is now fixed. This is exactly why “thorough testing” matters more than clever design claims.
- Treat `--variant-workers` as a genuine throughput lever now. On the local 10-core M4, the benchmarked gains were large enough to justify recommending more CPU for research once the sweep grid is big enough.
- Put post-exit TP/SL research inside the simulator, not in a separate analysis script. If the replay engine does not own the after-close follow-through data, the exported trade ledger will drift from the actual modeled execution path.
- Track post-exit behavior with a bounded number of future intrabar bars (`ANALYTICS_POST_EXIT_BARS`) and summarize it conservatively as best move, worst move, and realized close-to-close volatility. That is enough to study TP/SL quality without pretending minute candles are a full tick-level truth source.
- Store the research discipline in the repo, not in conversation memory. A real-money system needs an explicit optimization plan so later sessions do not improvise giant grids, optimize exits before entries, or report in-sample winners as if they were robust.
- Use one-family sweeps as the first pruning pass, then move to targeted pairwise interaction checks for survivor knobs. Full-factor brute force across everything is computationally expensive and intellectually sloppy.
- Simplify the live strategy contract instead of half-maintaining two philosophies. `entry_ready` is now the only tradeable signal tier; confirmed bars still advance history, but they no longer pretend to add a second actionable lifecycle.
- Keep residual momentum in the live engine, not just in research notes. The default ranking mode is now `basket_relative` because raw absolute momentum was too willing to collapse into generic alt beta.
- Add a blunt cluster cap (`MAX_POSITIONS_PER_CLUSTER`) before inventing more exotic correlation controls. A manual cluster map is imperfect, but it is still more honest than pretending raw position count alone controls concentration.
- Make long-horizon backtests honest about listing history. `BACKTEST_UNIVERSE_POLICY=window_available` is the default because pretending every current symbol existed across old windows is worse than running on a smaller active universe for that slice.
- Put walk-forward validation and live-vs-backtest reconciliation in the repo itself. If those workflows live only in chat, they will not be used consistently when real money is at risk.
- Keep the live trading contract brutally simple for now. `entry_ready` is the only tradeable tier, and confirmed-bar logic stays as state advancement and analytics instead of pretending to be a second entry philosophy.
- Move the default residual ranking from `basket_relative` to `cluster_relative`. Comparing a symbol to recent correlation peers is a better default than comparing it to the whole basket when the real problem is generic beta pile-up.
- Let cluster assignment be configurable (`manual`, `dynamic`, `hybrid`) and feed both the ranking reference and the exposure cap from the same current grouping. Static clusters alone are too stale; dynamic clusters alone need a safe fallback.
- Put richer entry diagnostics directly into live trade notes and backtest trade exports. If a trade cannot explain why it passed, later TP/SL or filter tuning turns into superstition.
- Add built-in stress profiles to the backtester instead of relying on ad hoc “mentally imagine worse costs” arguments. Real-money research needs a repeatable hostile-case path.
- Export the full walk-forward candidate leaderboard, not just the single winner. If the runner-up is nearly as good but much more stable, the repo should preserve that information.
- Do the first production-hardening pass around explicit controls and observability, not around a big repo rewrite. That means startup validation, manifests, health snapshots, runtime events, and drift checks before deeper architecture work.
- Keep the control-plane modules flat (`runtime_validation.py`, `runtime_monitor.py`, `monitor.py`) instead of inventing package layers. The repo benefits from clearer boundaries, not from enterprise cosplay.
- Add `OPERATOR_PAUSE_NEW_ENTRIES` as the simplest honest manual brake. If someone wants the bot to keep observing but stop taking fresh risk, that should be a first-class control, not a hacky env dance.
