from __future__ import annotations

import asyncio
import sqlite3
from collections import deque
from pathlib import Path

import aiohttp
import numpy as np

from alerting import TelegramNotifier
from config import Settings
from database import SignalDatabase
from signal_engine import SignalEngine
from state import ConfirmedObservation, IntrabarObservation, MarketState


def test_signal_engine_logs_full_cross_section(tmp_path: Path) -> None:
    asyncio.run(_exercise_signal_engine(tmp_path))


def test_signal_engine_only_sets_cooldown_after_successful_alert(tmp_path: Path) -> None:
    asyncio.run(_exercise_cooldown_behavior(tmp_path))


def test_signal_engine_handles_alert_exceptions_without_losing_logs(tmp_path: Path) -> None:
    asyncio.run(_exercise_alert_exception_behavior(tmp_path))


def test_signal_engine_emits_emerging_once_per_transition_and_confirmed_separately(tmp_path: Path) -> None:
    asyncio.run(_exercise_emerging_transition_behavior(tmp_path))


def test_signal_engine_can_disable_all_signal_telegram_alerts(tmp_path: Path) -> None:
    asyncio.run(_exercise_signal_alert_toggle(tmp_path))


def test_signal_engine_upgrades_confirmed_signal_with_persistence(tmp_path: Path) -> None:
    asyncio.run(_exercise_confirmed_persistence_behavior(tmp_path))


def test_signal_engine_builds_confirmed_summary_payload(tmp_path: Path) -> None:
    asyncio.run(_exercise_confirmed_summary_payload(tmp_path))


async def _exercise_signal_engine(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "signals.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    state = MarketState(settings=settings)

    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray([20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)], dtype=float)

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()

    async with aiohttp.ClientSession() as session:
        engine = SignalEngine(
            settings=settings,
            state=state,
            database=database,
            notifier=TelegramNotifier(session=session, bot_token=None, chat_id=None),
        )
        await engine.process(cycle_time_ms=timestamps[-1])

    with sqlite3.connect(settings.sqlite_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        ranked = connection.execute(
            "SELECT ticker, rank, price, dom_state, dom_change_pct FROM signals ORDER BY composite_score DESC"
        ).fetchall()
        assert count == 3
        assert ranked[0][0] == "AAAUSDT"
        assert ranked[0][1] == 1
        assert ranked[0][2] > 0
        assert ranked[0][3] in {"falling", "neutral", "rising"}
        assert isinstance(ranked[0][4], float)


async def _exercise_cooldown_behavior(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "cooldown.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray([20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)], dtype=float)

    class StubNotifier:
        def __init__(self, should_succeed: bool) -> None:
            self.should_succeed = should_succeed

        async def send(self, payload) -> bool:
            return self.should_succeed

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()

    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=StubNotifier(should_succeed=False),
    )
    await engine.process(cycle_time_ms=timestamps[-1])
    assert "AAAUSDT" not in state.last_alerted

    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=StubNotifier(should_succeed=True),
    )
    await engine.process(cycle_time_ms=timestamps[-1])
    assert state.last_alerted["AAAUSDT"].timestamp() == timestamps[-1] / 1000


async def _exercise_alert_exception_behavior(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "alert-errors.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray([20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)], dtype=float)

    class ExplodingNotifier:
        async def send(self, payload) -> bool:
            raise RuntimeError("network down")

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=ExplodingNotifier(),
    )
    await engine.process(cycle_time_ms=timestamps[-1])

    assert "AAAUSDT" not in state.last_alerted
    with sqlite3.connect(settings.sqlite_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert count == 3


async def _exercise_emerging_transition_behavior(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "emerging.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
        watchlist_telegram_enabled=True,
        top_n=1,
        emerging_top_n=1,
        entry_ready_top_n=1,
        watchlist_top_n=5,
        emerging_min_observations=3,
        emerging_min_rank_improvement=2,
        entry_ready_min_observations=5,
        entry_ready_min_rank_improvement=2,
        entry_ready_min_composite_gain=0.01,
        regime_thresholds={0: None, 1: None, 2: None, 3: None},
    )
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    next_timestamp = timestamps[-1] + settings.ticker_interval_ms
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray(
        [20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)],
        dtype=float,
    )

    class RecordingNotifier:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        async def send(self, payload) -> bool:
            if payload.ticker == "AAAUSDT":
                self.kinds.append(payload.signal_kind)
            return True

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    assert state.update_provisional("AAAUSDT", next_timestamp, strong[-1] + 1.5) is True
    watchlist_signals = await engine.process(cycle_time_ms=next_timestamp, stage="emerging")
    assert any(
        signal.ticker == "AAAUSDT" and signal.signal_kind == "watchlist"
        for signal in watchlist_signals
    )
    assert notifier.kinds == ["watchlist"]

    state.intrabar_observations["AAAUSDT"] = deque(
        [
            IntrabarObservation(observed_at_ms=next_timestamp - 20_000, rank=3, composite_score=0.1),
            IntrabarObservation(observed_at_ms=next_timestamp - 10_000, rank=2, composite_score=0.2),
        ],
        maxlen=state.intrabar_observations["AAAUSDT"].maxlen,
    )
    state.intrabar_state["AAAUSDT"] = "watchlist"

    assert state.update_provisional("AAAUSDT", next_timestamp, strong[-1] + 2.0) is True
    emerging_signals = await engine.process(cycle_time_ms=next_timestamp + 20_000, stage="emerging")
    assert any(
        signal.ticker == "AAAUSDT" and signal.signal_kind == "emerging"
        for signal in emerging_signals
    )
    assert notifier.kinds == ["watchlist", "emerging"]

    state.intrabar_observations["AAAUSDT"] = deque(
        [
            IntrabarObservation(observed_at_ms=next_timestamp - 40_000, rank=4, composite_score=0.05),
            IntrabarObservation(observed_at_ms=next_timestamp - 30_000, rank=3, composite_score=0.10),
            IntrabarObservation(observed_at_ms=next_timestamp - 20_000, rank=2, composite_score=0.15),
            IntrabarObservation(observed_at_ms=next_timestamp - 10_000, rank=1, composite_score=0.20),
        ],
        maxlen=state.intrabar_observations["AAAUSDT"].maxlen,
    )

    assert state.update_provisional("AAAUSDT", next_timestamp, strong[-1] + 2.5) is True
    entry_ready_signals = await engine.process(cycle_time_ms=next_timestamp + 40_000, stage="emerging")
    assert any(
        signal.ticker == "AAAUSDT" and signal.signal_kind == "entry_ready"
        for signal in entry_ready_signals
    )
    assert notifier.kinds == ["watchlist", "emerging", "entry_ready"]

    assert state.append_close("BTCUSDT", next_timestamp, btc_prices[-1] + 50.0) is True
    assert state.append_close("AAAUSDT", next_timestamp, strong[-1] + 2.5) is True
    assert state.append_close("BBBUSDT", next_timestamp, base[-1] + 0.02) is True
    assert state.append_close("CCCUSDT", next_timestamp, weak[-1] + 0.01) is True

    confirmed_signals = await engine.process(cycle_time_ms=next_timestamp, stage="confirmed")
    assert any(
        signal.ticker == "AAAUSDT" and signal.signal_kind == "confirmed"
        for signal in confirmed_signals
    )
    assert notifier.kinds == ["watchlist", "emerging", "entry_ready", "confirmed"]


async def _exercise_signal_alert_toggle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "signal-toggle.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
        telegram_signal_alerts_enabled=False,
        watchlist_telegram_enabled=True,
        top_n=1,
        emerging_top_n=1,
        entry_ready_top_n=1,
        watchlist_top_n=5,
        emerging_min_observations=3,
        emerging_min_rank_improvement=2,
        entry_ready_min_observations=5,
        entry_ready_min_rank_improvement=2,
        entry_ready_min_composite_gain=0.01,
        regime_thresholds={0: None, 1: None, 2: None, 3: None},
    )
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    next_timestamp = timestamps[-1] + settings.ticker_interval_ms
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray(
        [20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)],
        dtype=float,
    )

    class RecordingNotifier:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        async def send(self, payload) -> bool:
            self.kinds.append(payload.signal_kind)
            return True

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    assert state.update_provisional("AAAUSDT", next_timestamp, strong[-1] + 1.5) is True
    signals = await engine.process(cycle_time_ms=next_timestamp, stage="emerging")

    assert any(signal.ticker == "AAAUSDT" and signal.signal_kind == "watchlist" for signal in signals)
    assert notifier.kinds == []

    with sqlite3.connect(settings.sqlite_path) as connection:
        row_count = connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        watchlist_count = connection.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_kind = 'watchlist'"
        ).fetchone()[0]
        assert row_count >= 1
        assert watchlist_count >= 1


async def _exercise_confirmed_persistence_behavior(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "confirmed-persistence.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
        top_n=1,
        confirmed_persistence_window=3,
        confirmed_persistence_min_hits=2,
        confirmed_persistence_rank=2,
        regime_thresholds={0: None, 1: None, 2: None, 3: None},
    )
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    next_timestamp = timestamps[-1] + settings.ticker_interval_ms
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray(
        [20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)],
        dtype=float,
    )
    state.confirmed_observations["AAAUSDT"] = deque(
        [
            ConfirmedObservation(
                observed_at_ms=timestamps[-2],
                rank=2,
                composite_score=0.8,
                qualified=True,
            )
        ],
        maxlen=state.confirmed_observations["AAAUSDT"].maxlen,
    )

    class RecordingNotifier:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        async def send(self, payload) -> bool:
            if payload.ticker == "AAAUSDT":
                self.kinds.append(payload.signal_kind)
            return True

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    confirmed_signals = await engine.process(cycle_time_ms=next_timestamp, stage="confirmed")

    assert any(
        signal.ticker == "AAAUSDT" and signal.signal_kind == "confirmed_strong" and signal.persistence_hits == 2
        for signal in confirmed_signals
    )
    assert notifier.kinds == ["confirmed_strong"]
    with sqlite3.connect(settings.sqlite_path) as connection:
        rows = connection.execute(
            "SELECT signal_kind, persistence_hits FROM signals WHERE ticker = 'AAAUSDT'"
        ).fetchall()
    assert ("confirmed_strong", 2) in rows


async def _exercise_confirmed_summary_payload(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "confirmed-summary.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        telegram_bot_token=None,
        telegram_chat_id=None,
        summary_top_n=2,
        summary_bottom_n=2,
        regime_thresholds={0: None, 1: None, 2: None, 3: None},
    )
    state = MarketState(settings=settings)
    timestamps = [idx * settings.ticker_interval_ms for idx in range(settings.state_window)]
    btc_prices = [20_000 + idx * 50 for idx in range(settings.state_window)]
    strong = [100 + idx * 0.03 for idx in range(settings.state_window)]
    base = [100 + idx * 0.02 for idx in range(settings.state_window)]
    weak = [100 + idx * 0.01 for idx in range(settings.state_window)]

    state.replace_history("BTCUSDT", list(zip(timestamps, btc_prices)))
    state.replace_history("AAAUSDT", list(zip(timestamps, strong)))
    state.replace_history("BBBUSDT", list(zip(timestamps, base)))
    state.replace_history("CCCUSDT", list(zip(timestamps, weak)))
    state.global_state.btc_daily_closes = np.asarray(
        [20_000 + idx * 100 for idx in range(settings.btc_daily_lookback)],
        dtype=float,
    )

    class SilentNotifier:
        enabled = True

        async def send(self, payload) -> bool:
            return True

        async def send_summary(self, payload) -> bool:
            return True

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    engine = SignalEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=SilentNotifier(),
    )

    ranked_signals = await engine.process(cycle_time_ms=timestamps[-1], stage="confirmed")
    summary = engine.build_summary_payload(
        stage="confirmed",
        cycle_time_ms=timestamps[-1],
        ranked_signals=ranked_signals,
    )

    assert summary is not None
    assert len(summary.top_rankings) == 2
    assert len(summary.bottom_rankings) == 2
    assert summary.top_rankings[0].ticker == "AAAUSDT"
    assert {entry.ticker for entry in summary.bottom_rankings} == {"BBBUSDT", "CCCUSDT"}
