from __future__ import annotations

from pathlib import Path

from config import load_settings


def test_load_settings_reads_dotenv_and_overrides_existing_env(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LOG_LEVEL=debug\n"
        "EMERGING_COOLDOWN_MINUTES=15\n"
        "WATCHLIST_COOLDOWN_MINUTES=15\n"
        "BTCDOM_EMA_PERIOD=7\n"
        "ENTRY_READY_MIN_OBSERVATIONS=6\n"
        "EXECUTION_ENABLED=true\n"
        "DEMO_MODE=false\n"
        "TELEGRAM_SIGNAL_ALERTS_ENABLED=false\n"
        "ENTRY_NOTIONAL_USD=250\n"
        "RISK_PER_TRADE_PCT=0.015\n"
        "MAX_OPEN_POSITIONS=5\n"
        "INTRADAY_REGIME_FILTER_ENABLED=false\n"
        "INTRADAY_REGIME_LOOKBACK_BARS=20\n"
        "INTRADAY_REGIME_MIN_BREADTH=0.6\n"
        "INTRADAY_REGIME_MIN_PASS_COUNT=4\n"
        "TAKE_PROFIT_PCT=0.02\n"
        "STOP_LOSS_PCT=0.02\n"
        "TELEGRAM_CHAT_ID=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("EMERGING_COOLDOWN_MINUTES", raising=False)
    monkeypatch.delenv("WATCHLIST_COOLDOWN_MINUTES", raising=False)
    monkeypatch.delenv("ENTRY_READY_MIN_OBSERVATIONS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "from-env")

    settings = load_settings()

    assert settings.log_level == "DEBUG"
    assert settings.emerging_cooldown_minutes == 15
    assert settings.watchlist_cooldown_minutes == 15
    assert settings.btcdom_ema_period == 7
    assert settings.entry_ready_min_observations == 6
    assert settings.entry_ready_top_n == 4
    assert settings.entry_ready_cooldown_minutes == 15
    assert settings.execution_enabled is True
    assert settings.demo_mode is False
    assert settings.telegram_signal_alerts_enabled is False
    assert settings.entry_notional_usd == 250
    assert settings.risk_per_trade_pct == 0.015
    assert settings.max_open_positions == 5
    assert settings.intraday_regime_filter_enabled is False
    assert settings.intraday_regime_lookback_bars == 20
    assert settings.intraday_regime_min_breadth == 0.6
    assert settings.intraday_regime_min_pass_count == 4
    assert settings.take_profit_pct == 0.02
    assert settings.stop_loss_pct == 0.02
    assert settings.telegram_chat_id == "from-file"


def test_load_settings_reads_analytics_controls(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANALYTICS_ENABLED=true\n"
        "ANALYTICS_LOG_POSITION_MARKS=false\n"
        "ANALYTICS_LOG_PORTFOLIO_SNAPSHOTS=false\n"
        "ANALYTICS_PORTFOLIO_SNAPSHOT_ON_EMERGING=true\n"
        "ANALYTICS_POST_EXIT_BARS=24\n"
        "MAX_DAILY_STOP_LOSSES=2\n"
        "OPERATOR_PAUSE_NEW_ENTRIES=true\n"
        "RUNTIME_HEALTH_SNAPSHOT_ENABLED=false\n"
        "RUNTIME_HEALTH_SNAPSHOT_SECONDS=30\n"
        "RUNTIME_DRIFT_CHECK_ENABLED=false\n"
        "RUNTIME_DRIFT_CHECK_SECONDS=90\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.analytics_enabled is True
    assert settings.analytics_log_position_marks is False
    assert settings.analytics_log_portfolio_snapshots is False
    assert settings.analytics_portfolio_snapshot_on_emerging is True
    assert settings.analytics_post_exit_bars == 24
    assert settings.max_daily_stop_losses == 2
    assert settings.operator_pause_new_entries is True
    assert settings.runtime_health_snapshot_enabled is False
    assert settings.runtime_health_snapshot_seconds == 30
    assert settings.runtime_drift_check_enabled is False
    assert settings.runtime_drift_check_seconds == 90


def test_load_settings_reads_backtest_controls(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BACKTEST_MODE=true\n"
        "BACKTEST_STARTING_EQUITY_USD=250000\n"
        "BACKTEST_FEE_RATE=0.0008\n"
        "BACKTEST_SLIPPAGE_BPS=4\n"
        "BACKTEST_MAX_GROSS_EXPOSURE_MULTIPLE=1.2\n"
        "BACKTEST_INTRABAR_INTERVAL=1\n"
        "BACKTEST_CACHE_ENABLED=true\n"
        "BACKTEST_CACHE_PATH=/tmp/model050426-cache.sqlite3\n"
        "BACKTEST_RESEARCH_FAST=true\n"
        "BACKTEST_VARIANT_WORKERS=3\n"
        "BACKTEST_UNIVERSE_POLICY=strict\n"
        "BACKTEST_MIN_ACTIVE_UNIVERSE=12\n"
        "MAX_POSITIONS_PER_CLUSTER=2\n"
        "MOMENTUM_REFERENCE_MODE=cluster_relative\n"
        "CLUSTER_CORRELATION_LOOKBACK_BARS=64\n"
        "CLUSTER_CORRELATION_THRESHOLD=0.82\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.backtest_mode is True
    assert settings.backtest_starting_equity_usd == 250000
    assert settings.backtest_fee_rate == 0.0008
    assert settings.backtest_slippage_bps == 4
    assert settings.backtest_max_gross_exposure_multiple == 1.2
    assert settings.backtest_intrabar_interval == "1"
    assert settings.backtest_cache_enabled is True
    assert settings.backtest_cache_path == "/tmp/model050426-cache.sqlite3"
    assert settings.backtest_research_fast is True
    assert settings.backtest_variant_workers == 3
    assert settings.backtest_universe_policy == "strict"
    assert settings.backtest_min_active_universe == 12
    assert settings.max_positions_per_cluster == 2
    assert settings.momentum_reference_mode == "cluster_relative"
    assert settings.cluster_correlation_lookback_bars == 64
    assert settings.cluster_correlation_threshold == 0.82
