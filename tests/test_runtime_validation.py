from __future__ import annotations

import pytest

from config import Settings
from runtime_validation import (
    RuntimeConfigError,
    raise_for_invalid_runtime_settings,
    validate_runtime_settings,
)


def test_raise_for_invalid_runtime_settings_rejects_live_submit_without_credentials() -> None:
    settings = Settings(
        execution_enabled=True,
        execution_submit_orders=True,
        bybit_api_key=None,
        bybit_api_secret=None,
    )

    with pytest.raises(RuntimeConfigError):
        raise_for_invalid_runtime_settings(settings)


def test_validate_runtime_settings_warns_when_telegram_enabled_without_credentials() -> None:
    settings = Settings(
        telegram_signal_alerts_enabled=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )

    messages = validate_runtime_settings(settings, disable_telegram=False)

    assert any(message.code == "telegram.disabled_by_missing_credentials" for message in messages)


def test_validate_runtime_settings_accepts_disabled_telegram_override() -> None:
    settings = Settings(
        telegram_signal_alerts_enabled=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )

    messages = validate_runtime_settings(settings, disable_telegram=True)

    assert all(message.code != "telegram.disabled_by_missing_credentials" for message in messages)
