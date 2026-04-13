from __future__ import annotations

from dataclasses import dataclass

from config import Settings


class RuntimeConfigError(ValueError):
    pass


@dataclass(slots=True)
class ValidationMessage:
    level: str
    code: str
    message: str


VALID_MOMENTUM_REFERENCE_MODES = {
    "absolute",
    "btc_relative",
    "basket_relative",
    "cluster_relative",
}


def validate_runtime_settings(
    settings: Settings,
    *,
    disable_telegram: bool = False,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not settings.universe:
        messages.append(
            ValidationMessage("error", "universe.empty", "UNIVERSE must contain at least one symbol.")
        )
    if settings.queue_maxsize <= 0:
        messages.append(
            ValidationMessage("error", "queue.invalid", "QUEUE_MAXSIZE must be positive.")
        )
    if settings.take_profit_pct <= 0:
        messages.append(
            ValidationMessage("error", "tp.invalid", "TAKE_PROFIT_PCT must be positive.")
        )
    if settings.stop_loss_pct <= 0:
        messages.append(
            ValidationMessage("error", "sl.invalid", "STOP_LOSS_PCT must be positive.")
        )
    if settings.risk_per_trade_pct <= 0:
        messages.append(
            ValidationMessage(
                "error",
                "risk.invalid",
                "RISK_PER_TRADE_PCT must be positive when execution is enabled.",
            )
        )
    elif settings.risk_per_trade_pct > 0.05:
        messages.append(
            ValidationMessage(
                "warning",
                "risk.high",
                f"RISK_PER_TRADE_PCT={settings.risk_per_trade_pct:.4f} is aggressive for live trading.",
            )
        )
    if settings.execution_submit_orders and not settings.execution_enabled:
        messages.append(
            ValidationMessage(
                "error",
                "execution.submit_without_execution",
                "EXECUTION_SUBMIT_ORDERS=true requires EXECUTION_ENABLED=true.",
            )
        )
    if settings.execution_submit_orders and (not settings.bybit_api_key or not settings.bybit_api_secret):
        messages.append(
            ValidationMessage(
                "error",
                "execution.missing_credentials",
                "Live/demo order submission requires BYBIT_API_KEY and BYBIT_API_SECRET.",
            )
        )
    if settings.momentum_reference_mode not in VALID_MOMENTUM_REFERENCE_MODES:
        messages.append(
            ValidationMessage(
                "error",
                "momentum_reference_mode.invalid",
                f"Unsupported MOMENTUM_REFERENCE_MODE={settings.momentum_reference_mode}.",
            )
        )
    if settings.cluster_correlation_lookback_bars < 3:
        messages.append(
            ValidationMessage(
                "error",
                "cluster_lookback.invalid",
                "CLUSTER_CORRELATION_LOOKBACK_BARS must be at least 3.",
            )
        )
    if not 0 <= settings.cluster_correlation_threshold <= 1:
        messages.append(
            ValidationMessage(
                "error",
                "cluster_threshold.invalid",
                "CLUSTER_CORRELATION_THRESHOLD must be between 0 and 1.",
            )
        )
    if settings.runtime_health_snapshot_enabled and settings.runtime_health_snapshot_seconds <= 0:
        messages.append(
            ValidationMessage(
                "error",
                "health_snapshot.interval_invalid",
                "RUNTIME_HEALTH_SNAPSHOT_SECONDS must be positive when health snapshots are enabled.",
            )
        )
    if settings.runtime_drift_check_enabled and settings.runtime_drift_check_seconds <= 0:
        messages.append(
            ValidationMessage(
                "error",
                "drift_check.interval_invalid",
                "RUNTIME_DRIFT_CHECK_SECONDS must be positive when drift checks are enabled.",
            )
        )
    if (
        not disable_telegram
        and settings.telegram_signal_alerts_enabled
        and (not settings.telegram_bot_token or not settings.telegram_chat_id)
    ):
        messages.append(
            ValidationMessage(
                "warning",
                "telegram.disabled_by_missing_credentials",
                "Telegram signal alerts are enabled in config, but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.",
            )
        )
    return messages


def raise_for_invalid_runtime_settings(
    settings: Settings,
    *,
    disable_telegram: bool = False,
) -> list[ValidationMessage]:
    messages = validate_runtime_settings(settings, disable_telegram=disable_telegram)
    errors = [message for message in messages if message.level == "error"]
    if errors:
        raise RuntimeConfigError(
            "Invalid runtime configuration: "
            + "; ".join(f"{message.code}={message.message}" for message in errors)
        )
    return messages
