from __future__ import annotations

from config import Settings


_EPSILON = 1e-9


def break_even_trigger_pct(settings: Settings) -> float | None:
    if not settings.break_even_stop_enabled or settings.take_profit_pct <= 0:
        return None
    fraction = min(max(settings.break_even_stop_trigger_fraction_of_tp, 0.0), 1.0)
    return settings.take_profit_pct * fraction


def break_even_trigger_price(entry_price: float, settings: Settings) -> float | None:
    trigger_pct = break_even_trigger_pct(settings)
    if trigger_pct is None:
        return None
    return entry_price * (1.0 + trigger_pct)


def initial_take_profit_price(entry_price: float, settings: Settings) -> float:
    return entry_price * (1.0 + settings.take_profit_pct)


def initial_stop_loss_price(entry_price: float, settings: Settings) -> float:
    return entry_price * (1.0 - settings.stop_loss_pct)


def profit_ratchet_can_advance(
    settings: Settings,
    *,
    current_step: int,
    signal_kind: str | None,
) -> bool:
    if not settings.profit_ratchet_enabled:
        return False
    if settings.take_profit_pct <= 0:
        return False
    if settings.profit_ratchet_max_steps > 0 and current_step >= settings.profit_ratchet_max_steps:
        return False
    if settings.profit_ratchet_require_entry_ready and signal_kind != "entry_ready":
        return False
    return True


def profit_ratchet_prices(
    entry_price: float,
    settings: Settings,
    *,
    next_step: int,
) -> tuple[float, float]:
    if next_step < 1:
        raise ValueError("Profit ratchet steps start at 1")
    tp_multiple = next_step + 1
    sl_multiple = next_step - 1
    return (
        entry_price * (1.0 + (settings.take_profit_pct * tp_multiple)),
        entry_price * (1.0 + (settings.take_profit_pct * sl_multiple)),
    )


def stop_exit_event(
    *,
    entry_price: float,
    stop_loss_price: float,
    ratchet_step: int,
    break_even_stop_active: bool,
) -> str:
    if stop_loss_price > entry_price * (1.0 + _EPSILON):
        return "profit_ratchet_stop_exit"
    if break_even_stop_active or stop_loss_price >= entry_price * (1.0 - _EPSILON):
        return "break_even_stop_exit"
    if ratchet_step > 0 and stop_loss_price >= entry_price * (1.0 - _EPSILON):
        return "break_even_stop_exit"
    return "stop_loss_exit"


def infer_exit_event(
    *,
    entry_price: float,
    exit_price: float,
    take_profit_price: float,
    stop_loss_price: float,
    ratchet_step: int,
    break_even_stop_active: bool,
    explicit_reason: str | None = None,
) -> str:
    if explicit_reason in {"stale_timeout", "stale_timeout_hit"}:
        return "stale_timeout_exit"
    if take_profit_price > 0 and exit_price >= take_profit_price * (1.0 - _EPSILON):
        return "take_profit_exit"
    if stop_loss_price > 0 and exit_price <= stop_loss_price * (1.0 + _EPSILON):
        return stop_exit_event(
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            ratchet_step=ratchet_step,
            break_even_stop_active=break_even_stop_active,
        )
    return "exchange_exit"
