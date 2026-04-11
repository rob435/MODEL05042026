from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from config import Settings
from database import SignalDatabase

LOGGER = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_git_commit(cwd: str | None = None) -> str:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return output.strip() or "unknown"
    except Exception:
        return "unknown"


def build_config_fingerprint(settings: Settings) -> str:
    payload = {
        "execution_enabled": settings.execution_enabled,
        "execution_submit_orders": settings.execution_submit_orders,
        "demo_mode": settings.demo_mode,
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "max_open_positions": settings.max_open_positions,
        "max_positions_per_cluster": settings.max_positions_per_cluster,
        "max_entries_per_rebalance": settings.max_entries_per_rebalance,
        "take_profit_pct": settings.take_profit_pct,
        "stop_loss_pct": settings.stop_loss_pct,
        "max_daily_stop_losses": settings.max_daily_stop_losses,
        "operator_pause_new_entries": settings.operator_pause_new_entries,
        "momentum_reference_mode": settings.momentum_reference_mode,
        "momentum_reference_blend_btc_weight": settings.momentum_reference_blend_btc_weight,
        "cluster_assignment_mode": settings.cluster_assignment_mode,
        "cluster_correlation_lookback_bars": settings.cluster_correlation_lookback_bars,
        "cluster_correlation_threshold": settings.cluster_correlation_threshold,
        "intraday_regime_filter_enabled": settings.intraday_regime_filter_enabled,
        "intraday_regime_min_breadth": settings.intraday_regime_min_breadth,
        "intraday_regime_min_efficiency": settings.intraday_regime_min_efficiency,
        "intraday_regime_min_basket_return": settings.intraday_regime_min_basket_return,
        "intraday_regime_min_leadership_persistence": settings.intraday_regime_min_leadership_persistence,
        "intraday_regime_min_pass_count": settings.intraday_regime_min_pass_count,
        "entry_ready_top_n": settings.entry_ready_top_n,
        "entry_ready_min_observations": settings.entry_ready_min_observations,
        "entry_ready_min_rank_improvement": settings.entry_ready_min_rank_improvement,
        "entry_ready_min_composite_gain": settings.entry_ready_min_composite_gain,
        "hurst_cutoff": settings.hurst_cutoff,
        "universe": list(settings.universe),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(slots=True)
class RunManifest:
    run_id: str
    started_at: str
    git_commit: str
    config_fingerprint: str
    execution_enabled: bool
    execution_submit_orders: bool
    demo_mode: bool
    telegram_enabled: bool
    operator_pause_new_entries: bool
    universe_size: int
    momentum_reference_mode: str
    cluster_assignment_mode: str
    notes: str = ""


@dataclass(slots=True)
class RuntimeHealthSnapshot:
    run_id: str
    recorded_at: str
    bootstraps: int
    macro_refreshes: int
    processed_cycles: int
    processed_confirmed_cycles: int
    processed_emerging_cycles: int
    websocket_sessions: int
    websocket_failures: int
    queue_drops: int
    confirmed_queue_drops: int
    emerging_queue_drops: int
    confirmed_queue_size: int
    emerging_queue_size: int
    open_positions: int
    daily_stop_loss_count: int
    last_bootstrap_at: str | None
    last_macro_refresh_at: str | None
    last_provisional_event_at: str | None
    last_confirmed_event_at: str | None
    last_processed_confirmed_at: str | None
    last_processed_emerging_at: str | None
    last_drift_check_at: str | None
    drift_status: str
    operator_pause_new_entries: bool
    notes: str = ""


class RuntimeTracker:
    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid4().hex
        self.started_at = _utc_now_iso()
        self.last_bootstrap_at: str | None = None
        self.last_macro_refresh_at: str | None = None
        self.last_provisional_event_at: str | None = None
        self.last_confirmed_event_at: str | None = None
        self.last_processed_confirmed_at: str | None = None
        self.last_processed_emerging_at: str | None = None
        self.last_drift_check_at: str | None = None
        self.drift_status: str = "not_run"

    def note_bootstrap(self) -> None:
        self.last_bootstrap_at = _utc_now_iso()

    def note_macro_refresh(self) -> None:
        self.last_macro_refresh_at = _utc_now_iso()

    def note_websocket_event(self, *, confirmed: bool) -> None:
        if confirmed:
            self.last_confirmed_event_at = _utc_now_iso()
        else:
            self.last_provisional_event_at = _utc_now_iso()

    def note_processed_cycle(self, stage: str) -> None:
        now = _utc_now_iso()
        if stage == "confirmed":
            self.last_processed_confirmed_at = now
        else:
            self.last_processed_emerging_at = now

    def note_drift_check(self, status: str) -> None:
        self.last_drift_check_at = _utc_now_iso()
        self.drift_status = status


def build_run_manifest(
    settings: Settings,
    *,
    disable_telegram: bool,
    cwd: str | None = None,
) -> RunManifest:
    return RunManifest(
        run_id=uuid4().hex,
        started_at=_utc_now_iso(),
        git_commit=_safe_git_commit(cwd),
        config_fingerprint=build_config_fingerprint(settings),
        execution_enabled=settings.execution_enabled,
        execution_submit_orders=settings.execution_submit_orders,
        demo_mode=settings.demo_mode,
        telegram_enabled=bool((not disable_telegram) and settings.telegram_bot_token and settings.telegram_chat_id),
        operator_pause_new_entries=settings.operator_pause_new_entries,
        universe_size=len(settings.universe),
        momentum_reference_mode=settings.momentum_reference_mode,
        cluster_assignment_mode=settings.cluster_assignment_mode,
    )


async def build_health_snapshot(
    *,
    database: SignalDatabase,
    tracker: RuntimeTracker,
    stats,
    confirmed_queue: asyncio.Queue[int],
    emerging_queue: asyncio.Queue[int],
    operator_pause_new_entries: bool,
) -> RuntimeHealthSnapshot:
    open_positions = await database.list_open_positions()
    utc_day = _utc_now_iso()[:10]
    daily_stop_loss_count = await database.count_trade_exit_events_for_day("stop_loss_exit", utc_day)
    return RuntimeHealthSnapshot(
        run_id=tracker.run_id,
        recorded_at=_utc_now_iso(),
        bootstraps=stats.bootstraps,
        macro_refreshes=stats.macro_refreshes,
        processed_cycles=stats.processed_cycles,
        processed_confirmed_cycles=stats.processed_confirmed_cycles,
        processed_emerging_cycles=stats.processed_emerging_cycles,
        websocket_sessions=stats.websocket_sessions,
        websocket_failures=stats.websocket_failures,
        queue_drops=stats.queue_drops,
        confirmed_queue_drops=stats.confirmed_queue_drops,
        emerging_queue_drops=stats.emerging_queue_drops,
        confirmed_queue_size=confirmed_queue.qsize(),
        emerging_queue_size=emerging_queue.qsize(),
        open_positions=len(open_positions),
        daily_stop_loss_count=daily_stop_loss_count,
        last_bootstrap_at=tracker.last_bootstrap_at,
        last_macro_refresh_at=tracker.last_macro_refresh_at,
        last_provisional_event_at=tracker.last_provisional_event_at,
        last_confirmed_event_at=tracker.last_confirmed_event_at,
        last_processed_confirmed_at=tracker.last_processed_confirmed_at,
        last_processed_emerging_at=tracker.last_processed_emerging_at,
        last_drift_check_at=tracker.last_drift_check_at,
        drift_status=tracker.drift_status,
        operator_pause_new_entries=operator_pause_new_entries,
    )


async def health_snapshot_loop(
    *,
    database: SignalDatabase,
    tracker: RuntimeTracker,
    stats,
    confirmed_queue: asyncio.Queue[int],
    emerging_queue: asyncio.Queue[int],
    stop_event: asyncio.Event,
    settings: Settings,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.runtime_health_snapshot_seconds,
            )
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        snapshot = await build_health_snapshot(
            database=database,
            tracker=tracker,
            stats=stats,
            confirmed_queue=confirmed_queue,
            emerging_queue=emerging_queue,
            operator_pause_new_entries=settings.operator_pause_new_entries,
        )
        await database.log_runtime_health_snapshot(**asdict(snapshot))
        LOGGER.info(
            "Health snapshot open_positions=%s confirmed_q=%s emerging_q=%s drift=%s",
            snapshot.open_positions,
            snapshot.confirmed_queue_size,
            snapshot.emerging_queue_size,
            snapshot.drift_status,
        )


async def drift_monitor_loop(
    *,
    database: SignalDatabase,
    execution_engine,
    tracker: RuntimeTracker,
    stop_event: asyncio.Event,
    settings: Settings,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.runtime_drift_check_seconds,
            )
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        status, detail = await execution_engine.detect_position_drift()
        tracker.note_drift_check(status)
        if status == "mismatch":
            await database.log_runtime_event(
                run_id=tracker.run_id,
                created_at=_utc_now_iso(),
                severity="warning",
                component="runtime_monitor",
                event_type="position_drift",
                detail=detail,
            )
            LOGGER.warning("Position drift detected: %s", detail)
