from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from universe import DEFAULT_UNIVERSE


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


def _get_symbols(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return list(default)
    return [token.strip().upper() for token in value.split(",") if token.strip()]


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ[key] = value.strip()


@dataclass(slots=True)
class Settings:
    bybit_rest_base_url: str = "https://api.bybit.com"
    bybit_ws_base_url: str = "wss://stream.bybit.com/v5/public/linear"
    bybit_trade_base_url: str = "https://api-demo.bybit.com"
    bybit_category: str = "linear"
    execution_enabled: bool = False
    demo_mode: bool = True
    execution_submit_orders: bool = False
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None
    entry_notional_usd: float = 100.0
    risk_per_trade_pct: float = 0.01
    max_open_positions: int = 3
    max_entries_per_rebalance: int = 0
    take_profit_pct: float = 0.03
    stop_loss_pct: float = 0.02
    max_daily_stop_losses: int = 0
    exit_on_lost_confirmed: bool = False
    bybit_recv_window: int = 5000
    trade_fill_poll_attempts: int = 10
    trade_fill_poll_delay_seconds: float = 0.5
    binance_futures_base_url: str = "https://fapi.binance.com"
    candle_interval: str = "15"
    candle_interval_minutes: int = 15
    state_window: int = 288
    btc_daily_lookback: int = 220
    btc_vol_lookback: int = 30
    btcdom_symbol: str = "BTCDOMUSDT"
    btcdom_interval: str = "1h"
    btcdom_history_lookback: int = 96
    btcdom_ema_period: int = 5
    btcdom_state_lookback_bars: int = 4
    btcdom_neutral_threshold_pct: float = 0.002
    momentum_lookback: int = 48
    momentum_skip: int = 4
    curvature_ma_window: int = 8
    curvature_signal_window: int = 6
    hurst_window: int = 96
    hurst_cutoff: float = 0.55
    top_n: int = 3
    watchlist_top_n: int = 8
    emerging_top_n: int = 5
    entry_ready_top_n: int = 4
    confirmed_persistence_window: int = 3
    confirmed_persistence_min_hits: int = 2
    confirmed_persistence_rank: int = 3
    ranking_log_top_n: int = 5
    telegram_signal_alerts_enabled: bool = True
    telegram_summary_enabled: bool = True
    summary_top_n: int = 5
    summary_bottom_n: int = 5
    momentum_weight: float = 0.85
    curvature_weight: float = 0.15
    momentum_z_clip: float = 3.0
    curvature_z_clip: float = 2.5
    confirmed_stability_weight: float = 0.25
    cooldown_hours: int = 12
    emerging_cooldown_minutes: int = 60
    watchlist_cooldown_minutes: int = 30
    entry_ready_cooldown_minutes: int = 15
    watchlist_telegram_enabled: bool = False
    emerging_min_observations: int = 3
    emerging_min_rank_improvement: int = 2
    entry_ready_min_observations: int = 4
    entry_ready_min_rank_improvement: int = 2
    entry_ready_min_composite_gain: float = 0.02
    min_volatility: float = 1e-5
    btc_realized_vol_threshold: float = 0.65
    bootstrap_concurrency: int = 8
    rate_limit_retries: int = 6
    rate_limit_backoff_seconds: float = 2.0
    websocket_ping_seconds: int = 20
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    cycle_settle_seconds: float = 2.0
    emerging_settle_seconds: float = 0.5
    emerging_interval_seconds: float = 10.0
    macro_refresh_seconds: int = 3600
    queue_maxsize: int = 4096
    sqlite_path: str = "signals.sqlite3"
    analytics_enabled: bool = True
    analytics_log_position_marks: bool = True
    analytics_log_portfolio_snapshots: bool = True
    analytics_portfolio_snapshot_on_emerging: bool = False
    analytics_post_exit_bars: int = 24
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    log_level: str = "INFO"
    universe: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    regime_thresholds: dict[int, float | None] = field(
        default_factory=lambda: {3: 0.5, 2: 0.8, 1: 1.2, 0: None}
    )
    dominance_score_adjustments: dict[int, float] = field(
        default_factory=lambda: {-1: -0.15, 0: 0.0, 1: 0.35}
    )

    @property
    def tracked_symbols(self) -> list[str]:
        symbols = list(dict.fromkeys(self.universe + ["BTCUSDT"]))
        return symbols

    @property
    def ticker_interval_ms(self) -> int:
        return self.candle_interval_minutes * 60 * 1000

    @property
    def ticker_history(self) -> int:
        return self.state_window

    @property
    def btc_history(self) -> int:
        return self.btc_daily_lookback

    @property
    def database_path(self) -> str:
        return self.sqlite_path


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        bybit_rest_base_url=os.getenv("BYBIT_REST_BASE_URL", "https://api.bybit.com"),
        bybit_ws_base_url=os.getenv(
            "BYBIT_WS_BASE_URL", "wss://stream.bybit.com/v5/public/linear"
        ),
        bybit_trade_base_url=os.getenv("BYBIT_TRADE_BASE_URL", "https://api-demo.bybit.com"),
        bybit_category=os.getenv("BYBIT_CATEGORY", "linear"),
        execution_enabled=_get_bool("EXECUTION_ENABLED", False),
        demo_mode=_get_bool("DEMO_MODE", True),
        execution_submit_orders=_get_bool("EXECUTION_SUBMIT_ORDERS", False),
        bybit_api_key=os.getenv("BYBIT_API_KEY"),
        bybit_api_secret=os.getenv("BYBIT_API_SECRET"),
        entry_notional_usd=_get_float("ENTRY_NOTIONAL_USD", 100.0),
        risk_per_trade_pct=_get_float("RISK_PER_TRADE_PCT", 0.01),
        max_open_positions=_get_int("MAX_OPEN_POSITIONS", 3),
        max_entries_per_rebalance=_get_int("MAX_ENTRIES_PER_REBALANCE", 0),
        take_profit_pct=_get_float("TAKE_PROFIT_PCT", 0.03),
        stop_loss_pct=_get_float("STOP_LOSS_PCT", 0.02),
        max_daily_stop_losses=_get_int("MAX_DAILY_STOP_LOSSES", 0),
        exit_on_lost_confirmed=_get_bool("EXIT_ON_LOST_CONFIRMED", False),
        bybit_recv_window=_get_int("BYBIT_RECV_WINDOW", 5000),
        trade_fill_poll_attempts=_get_int("TRADE_FILL_POLL_ATTEMPTS", 10),
        trade_fill_poll_delay_seconds=_get_float("TRADE_FILL_POLL_DELAY_SECONDS", 0.5),
        binance_futures_base_url=os.getenv(
            "BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com"
        ),
        candle_interval=os.getenv("CANDLE_INTERVAL", "15"),
        candle_interval_minutes=_get_int("CANDLE_INTERVAL_MINUTES", 15),
        state_window=_get_int("STATE_WINDOW", 288),
        btc_daily_lookback=_get_int("BTC_DAILY_LOOKBACK", 220),
        btc_vol_lookback=_get_int("BTC_VOL_LOOKBACK", 30),
        btcdom_symbol=os.getenv("BTCDOM_SYMBOL", "BTCDOMUSDT").upper(),
        btcdom_interval=os.getenv("BTCDOM_INTERVAL", "1h"),
        btcdom_history_lookback=_get_int("BTCDOM_HISTORY_LOOKBACK", 96),
        btcdom_ema_period=_get_int("BTCDOM_EMA_PERIOD", 5),
        btcdom_state_lookback_bars=_get_int("BTCDOM_STATE_LOOKBACK_BARS", 4),
        btcdom_neutral_threshold_pct=_get_float("BTCDOM_NEUTRAL_THRESHOLD_PCT", 0.002),
        momentum_lookback=_get_int("MOMENTUM_LOOKBACK", 48),
        momentum_skip=_get_int("MOMENTUM_SKIP", 4),
        curvature_ma_window=_get_int("CURVATURE_MA_WINDOW", 8),
        curvature_signal_window=_get_int("CURVATURE_SIGNAL_WINDOW", 6),
        hurst_window=_get_int("HURST_WINDOW", 96),
        hurst_cutoff=_get_float("HURST_CUTOFF", 0.55),
        top_n=_get_int("TOP_N", 3),
        watchlist_top_n=_get_int("WATCHLIST_TOP_N", 8),
        emerging_top_n=_get_int("EMERGING_TOP_N", 5),
        entry_ready_top_n=_get_int("ENTRY_READY_TOP_N", 4),
        confirmed_persistence_window=_get_int("CONFIRMED_PERSISTENCE_WINDOW", 3),
        confirmed_persistence_min_hits=_get_int("CONFIRMED_PERSISTENCE_MIN_HITS", 2),
        confirmed_persistence_rank=_get_int("CONFIRMED_PERSISTENCE_RANK", 3),
        ranking_log_top_n=_get_int("RANKING_LOG_TOP_N", 5),
        telegram_signal_alerts_enabled=_get_bool("TELEGRAM_SIGNAL_ALERTS_ENABLED", True),
        telegram_summary_enabled=_get_bool("TELEGRAM_SUMMARY_ENABLED", True),
        summary_top_n=_get_int("SUMMARY_TOP_N", 5),
        summary_bottom_n=_get_int("SUMMARY_BOTTOM_N", 5),
        momentum_weight=_get_float("MOMENTUM_WEIGHT", 0.85),
        curvature_weight=_get_float("CURVATURE_WEIGHT", 0.15),
        momentum_z_clip=_get_float("MOMENTUM_Z_CLIP", 3.0),
        curvature_z_clip=_get_float("CURVATURE_Z_CLIP", 2.5),
        confirmed_stability_weight=_get_float("CONFIRMED_STABILITY_WEIGHT", 0.25),
        cooldown_hours=_get_int("COOLDOWN_HOURS", 12),
        emerging_cooldown_minutes=_get_int("EMERGING_COOLDOWN_MINUTES", 60),
        watchlist_cooldown_minutes=_get_int("WATCHLIST_COOLDOWN_MINUTES", 30),
        entry_ready_cooldown_minutes=_get_int("ENTRY_READY_COOLDOWN_MINUTES", 15),
        watchlist_telegram_enabled=_get_bool("WATCHLIST_TELEGRAM_ENABLED", False),
        emerging_min_observations=_get_int("EMERGING_MIN_OBSERVATIONS", 3),
        emerging_min_rank_improvement=_get_int("EMERGING_MIN_RANK_IMPROVEMENT", 2),
        entry_ready_min_observations=_get_int("ENTRY_READY_MIN_OBSERVATIONS", 4),
        entry_ready_min_rank_improvement=_get_int("ENTRY_READY_MIN_RANK_IMPROVEMENT", 2),
        entry_ready_min_composite_gain=_get_float("ENTRY_READY_MIN_COMPOSITE_GAIN", 0.02),
        min_volatility=_get_float("MIN_VOLATILITY", 1e-5),
        btc_realized_vol_threshold=_get_float("BTC_REALIZED_VOL_THRESHOLD", 0.65),
        bootstrap_concurrency=_get_int("BOOTSTRAP_CONCURRENCY", 8),
        rate_limit_retries=_get_int("RATE_LIMIT_RETRIES", 6),
        rate_limit_backoff_seconds=_get_float("RATE_LIMIT_BACKOFF_SECONDS", 2.0),
        websocket_ping_seconds=_get_int("WEBSOCKET_PING_SECONDS", 20),
        reconnect_base_delay=_get_float("RECONNECT_BASE_DELAY", 1.0),
        reconnect_max_delay=_get_float("RECONNECT_MAX_DELAY", 30.0),
        cycle_settle_seconds=_get_float("CYCLE_SETTLE_SECONDS", 2.0),
        emerging_settle_seconds=_get_float("EMERGING_SETTLE_SECONDS", 0.5),
        emerging_interval_seconds=_get_float("EMERGING_INTERVAL_SECONDS", 10.0),
        macro_refresh_seconds=_get_int("MACRO_REFRESH_SECONDS", 3600),
        queue_maxsize=_get_int("QUEUE_MAXSIZE", 4096),
        sqlite_path=os.getenv("SQLITE_PATH", "signals.sqlite3"),
        analytics_enabled=_get_bool("ANALYTICS_ENABLED", True),
        analytics_log_position_marks=_get_bool("ANALYTICS_LOG_POSITION_MARKS", True),
        analytics_log_portfolio_snapshots=_get_bool("ANALYTICS_LOG_PORTFOLIO_SNAPSHOTS", True),
        analytics_portfolio_snapshot_on_emerging=_get_bool(
            "ANALYTICS_PORTFOLIO_SNAPSHOT_ON_EMERGING",
            False,
        ),
        analytics_post_exit_bars=_get_int("ANALYTICS_POST_EXIT_BARS", 24),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        universe=_get_symbols("UNIVERSE", list(DEFAULT_UNIVERSE)),
    )
