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
        "ENTRY_NOTIONAL_USD=250\n"
        "RISK_PER_TRADE_PCT=0.015\n"
        "TAKE_PROFIT_PCT=0.03\n"
        "STOP_LOSS_PCT=0.01\n"
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
    assert settings.entry_notional_usd == 250
    assert settings.risk_per_trade_pct == 0.015
    assert settings.take_profit_pct == 0.03
    assert settings.stop_loss_pct == 0.01
    assert settings.telegram_chat_id == "from-file"
