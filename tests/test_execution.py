import asyncio
import sqlite3
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from config import Settings
from database import SignalDatabase
from execution import ExecutionEngine
from exchange import ClosedPnlRecord, InstrumentSpec, VenuePosition
from signal_engine import RankedSignal
from state import MarketState


class RecordingNotifier:
    enabled = True

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def send_execution(self, payload) -> bool:
        self.events.append((payload.event, payload.ticker))
        return True


def test_execution_engine_enters_and_takes_profit_with_telegram(tmp_path: Path) -> None:
    asyncio.run(_exercise_execution_take_profit_lifecycle(tmp_path))


def test_execution_engine_stops_out_with_telegram(tmp_path: Path) -> None:
    asyncio.run(_exercise_stop_loss_lifecycle(tmp_path))


def test_execution_engine_closes_stale_position_on_deadline(tmp_path: Path) -> None:
    asyncio.run(_exercise_stale_timeout_lifecycle(tmp_path))


def test_execution_engine_arms_and_hits_break_even_stop(tmp_path: Path) -> None:
    asyncio.run(_exercise_break_even_stop_lifecycle(tmp_path))


def test_execution_engine_allows_multiple_tickers_but_only_one_position_per_ticker(tmp_path: Path) -> None:
    asyncio.run(_exercise_per_ticker_entry_guard(tmp_path))


def test_execution_engine_limits_entries_per_rebalance(tmp_path: Path) -> None:
    asyncio.run(_exercise_max_entries_per_rebalance(tmp_path))


def test_execution_engine_respects_max_open_positions(tmp_path: Path) -> None:
    asyncio.run(_exercise_max_open_positions(tmp_path))


def test_execution_engine_respects_max_positions_per_cluster(tmp_path: Path) -> None:
    asyncio.run(_exercise_max_positions_per_cluster(tmp_path))


def test_execution_engine_resets_daily_stop_loss_limit_on_new_utc_day(tmp_path: Path) -> None:
    asyncio.run(_exercise_daily_stop_loss_reset(tmp_path))


def test_execution_engine_honors_operator_pause(tmp_path: Path) -> None:
    asyncio.run(_exercise_operator_pause(tmp_path))


def test_execution_engine_places_real_demo_order_and_syncs_exchange_exit(tmp_path: Path) -> None:
    asyncio.run(_exercise_live_demo_path(tmp_path))


def test_execution_engine_updates_live_stop_to_break_even(tmp_path: Path) -> None:
    asyncio.run(_exercise_live_break_even_stop_update(tmp_path))


def test_execution_engine_skips_live_entry_when_venue_position_exists(tmp_path: Path) -> None:
    asyncio.run(_exercise_live_duplicate_position_guard(tmp_path))


def test_execution_engine_detects_position_drift(tmp_path: Path) -> None:
    asyncio.run(_exercise_detect_position_drift(tmp_path))


def test_execution_engine_profit_ratchet_advances_and_exits_on_latched_stop(tmp_path: Path) -> None:
    asyncio.run(_exercise_profit_ratchet_lifecycle(tmp_path))


async def _exercise_execution_take_profit_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution.db"),
        universe=["AAAUSDT", "BBBUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )
    state.replace_history(
        "BBBUSDT",
        [(0, 80.0), (settings.ticker_interval_ms, 81.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    entry_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in entry_actions] == [("enter_long", "AAAUSDT")]

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 103.10) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in exit_actions] == [
        ("exit_long", "AAAUSDT")
    ]

    with sqlite3.connect(settings.sqlite_path) as connection:
        order_counts = dict(
            connection.execute(
                "SELECT lifecycle, COUNT(*) FROM orders GROUP BY lifecycle"
            ).fetchall()
        )
        position = connection.execute(
            """
            SELECT status, entry_price, exit_price
            FROM positions
            WHERE ticker = 'AAAUSDT'
            """
        ).fetchone()

    assert order_counts == {"entry": 1, "exit": 1}
    assert position == ("closed", 101.0, 103.1)
    assert notifier.events == [("enter_long", "AAAUSDT"), ("take_profit_exit", "AAAUSDT")]


async def _exercise_stop_loss_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-stop.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 98.90) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in exit_actions] == [
        ("exit_long", "AAAUSDT")
    ]
    assert notifier.events == [("enter_long", "AAAUSDT"), ("stop_loss_exit", "AAAUSDT")]

    with sqlite3.connect(settings.sqlite_path) as connection:
        position = connection.execute(
            "SELECT status, exit_price FROM positions WHERE ticker = 'AAAUSDT'"
        ).fetchone()
    assert position == ("closed", 98.9)


async def _exercise_stale_timeout_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-stale.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
        stale_position_max_minutes=240,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    current_time = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)

    def fake_now(*, cycle_time_ms, stage):
        return current_time

    execution._now = fake_now  # type: ignore[method-assign]

    await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    current_time = datetime(2026, 4, 9, 16, 0, tzinfo=timezone.utc)
    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 101.5) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 18,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker, action.detail) for action in exit_actions] == [
        ("exit_long", "AAAUSDT", "stale_timeout_hit")
    ]

    with sqlite3.connect(settings.sqlite_path) as connection:
        position = connection.execute(
            "SELECT status, exit_price, notes FROM positions WHERE ticker = 'AAAUSDT'"
        ).fetchone()
    assert position[0] == "closed"
    assert position[1] == 101.5
    assert "stale_timeout_hit" in position[2]
    assert notifier.events == [("enter_long", "AAAUSDT"), ("stale_timeout_exit", "AAAUSDT")]


async def _exercise_break_even_stop_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-break-even.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
        break_even_stop_enabled=True,
        break_even_stop_trigger_fraction_of_tp=0.75,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 102.6) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True
    arm_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert arm_actions == []

    with sqlite3.connect(settings.sqlite_path) as connection:
        armed_flag = connection.execute(
            "SELECT break_even_stop_active FROM positions WHERE ticker = 'AAAUSDT'"
        ).fetchone()[0]
    assert armed_flag == 1

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 100.9) is True
    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 3,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker, action.detail) for action in exit_actions] == [
        ("exit_long", "AAAUSDT", "break_even_stop_hit")
    ]
    assert notifier.events == [("enter_long", "AAAUSDT"), ("break_even_stop_exit", "AAAUSDT")]


async def _exercise_profit_ratchet_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-profit-ratchet.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
        profit_ratchet_enabled=True,
        profit_ratchet_max_steps=2,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
    )

    def ratchet_signal(price: float) -> RankedSignal:
        return RankedSignal(
            stage="emerging",
            signal_kind="entry_ready",
            ticker="AAAUSDT",
            current_price=price,
            momentum_z=2.0,
            curvature=0.2,
            hurst=0.7,
            regime_score=2,
            composite_score=1.4,
            rank=1,
            persistence_hits=0,
            alerted=False,
        )

    await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[ratchet_signal(101.0)],
    )

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 103.2) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True
    first_ratchet_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[ratchet_signal(103.2)],
    )
    assert [(action.action, action.ticker) for action in first_ratchet_actions] == [
        ("skip_entry", "AAAUSDT")
    ]

    with sqlite3.connect(settings.sqlite_path) as connection:
        first_ratchet = connection.execute(
            """
            SELECT take_profit_price, stop_loss_price, profit_ratchet_step, profit_ratchet_adjustments, break_even_stop_active
            FROM positions
            WHERE ticker = 'AAAUSDT'
            """
        ).fetchone()
    assert first_ratchet == (105.04, 101.0, 1, 1, 1)

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 105.3) is True
    second_ratchet_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[ratchet_signal(105.3)],
    )
    assert [(action.action, action.ticker) for action in second_ratchet_actions] == [
        ("skip_entry", "AAAUSDT")
    ]

    with sqlite3.connect(settings.sqlite_path) as connection:
        second_ratchet = connection.execute(
            """
            SELECT take_profit_price, stop_loss_price, profit_ratchet_step, profit_ratchet_adjustments
            FROM positions
            WHERE ticker = 'AAAUSDT'
            """
        ).fetchone()
        ratchet_events = connection.execute(
            """
            SELECT event_type, detail
            FROM runtime_events
            WHERE ticker = 'AAAUSDT' AND event_type = 'profit_ratchet_advanced'
            ORDER BY id
            """
        ).fetchall()
    assert second_ratchet == (107.06, 103.02, 2, 2)
    assert [detail for _, detail in ratchet_events] == [
        "step=1 ticker=AAAUSDT signal_kind=entry_ready tp=105.040000 sl=101.000000",
        "step=2 ticker=AAAUSDT signal_kind=entry_ready tp=107.060000 sl=103.020000",
    ]

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 103.0) is True
    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker, action.detail) for action in exit_actions] == [
        ("exit_long", "AAAUSDT", "profit_ratchet_stop_hit")
    ]
    assert notifier.events == [("enter_long", "AAAUSDT"), ("profit_ratchet_stop_exit", "AAAUSDT")]


async def _exercise_per_ticker_entry_guard(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-cap.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
    )
    state = MarketState(settings=settings)
    for symbol, price in (
        ("BTCUSDT", 20_000.0),
        ("AAAUSDT", 100.0),
        ("BBBUSDT", 90.0),
        ("CCCUSDT", 80.0),
    ):
        state.replace_history(
            symbol,
            [(0, price), (settings.ticker_interval_ms, price + 1.0)],
        )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            ),
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="BBBUSDT",
                current_price=91.0,
                momentum_z=1.5,
                curvature=0.15,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=2,
                persistence_hits=0,
                alerted=False,
            ),
        ],
    )

    assert [(action.action, action.ticker) for action in actions] == [
        ("enter_long", "AAAUSDT"),
        ("enter_long", "BBBUSDT"),
    ]

    with sqlite3.connect(settings.sqlite_path) as connection:
        open_positions = connection.execute(
            "SELECT ticker FROM positions WHERE status = 'open' ORDER BY ticker"
        ).fetchall()
    assert open_positions == [("AAAUSDT",), ("BBBUSDT",)]

    follow_up_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 3,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in follow_up_actions] == [
        ("skip_entry", "AAAUSDT")
    ]


async def _exercise_live_demo_path(tmp_path: Path) -> None:
    class FakeLiveClient:
        def __init__(self) -> None:
            self.position_open = False
            self.stops: list[tuple[str, str, str]] = []
            self.last_quantity: Decimal | None = None

        def enabled(self) -> bool:
            return True

        async def get_wallet_balance(self):
            return type(
                "WalletBalance",
                (),
                {
                    "total_equity_usd": Decimal("1000"),
                    "total_available_balance_usd": Decimal("1000"),
                },
            )()

        async def fetch_instrument_spec(self, symbol: str) -> InstrumentSpec:
            return InstrumentSpec(
                symbol=symbol,
                qty_step=Decimal("0.001"),
                min_order_qty=Decimal("0.001"),
                tick_size=Decimal("0.1"),
            )

        async def place_market_order(
            self,
            *,
            symbol: str,
            side: str,
            quantity: Decimal,
            order_link_id: str,
            reduce_only: bool = False,
        ):
            self.last_quantity = quantity
            return type("Ack", (), {"order_id": "demo-order-1", "order_link_id": order_link_id})()

        async def wait_for_position(self, symbol: str) -> VenuePosition:
            self.position_open = True
            return VenuePosition(
                symbol=symbol,
                side="Buy",
                size=Decimal("1.980"),
                avg_price=Decimal("101.0"),
                position_idx=0,
            )

        async def set_trading_stop(self, *, symbol: str, position_idx: int, take_profit: Decimal, stop_loss: Decimal) -> None:
            self.stops.append((symbol, format(take_profit, "f"), format(stop_loss, "f")))

        async def get_position(self, symbol: str):
            if self.position_open:
                return VenuePosition(
                    symbol=symbol,
                    side="Buy",
                    size=Decimal("1.980"),
                    avg_price=Decimal("101.0"),
                    position_idx=0,
                )
            return None

        async def get_latest_closed_pnl(self, symbol: str):
            return ClosedPnlRecord(
                symbol=symbol,
                order_id="venue-exit-1",
                avg_entry_price=Decimal("101.0"),
                avg_exit_price=Decimal("103.1"),
                closed_pnl=Decimal("4.158"),
                updated_time_ms=4_102_444_800_000,
            )

    settings = Settings(
        sqlite_path=str(tmp_path / "execution-live.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=True,
        risk_per_trade_pct=0.01,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    notifier = RecordingNotifier()
    fake_client = FakeLiveClient()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        notifier=notifier,
        client=fake_client,
    )

    entry_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in entry_actions] == [("enter_long", "AAAUSDT")]
    assert fake_client.last_quantity == Decimal("4.950")
    assert fake_client.stops == [("AAAUSDT", "103.0", "99.0")]

    fake_client.position_open = False
    sync_actions = await execution.process_cycle(
        stage="confirmed",
        cycle_time_ms=settings.ticker_interval_ms * 3,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in sync_actions] == [("exit_synced", "AAAUSDT")]

    with sqlite3.connect(settings.sqlite_path) as connection:
        order_statuses = connection.execute(
            "SELECT lifecycle, status FROM orders ORDER BY id"
        ).fetchall()
        requested_notional = connection.execute(
            "SELECT requested_notional_usd FROM orders WHERE lifecycle = 'entry'"
        ).fetchone()[0]
        position = connection.execute(
            "SELECT status, entry_price, exit_price FROM positions WHERE ticker = 'AAAUSDT'"
        ).fetchone()

    assert order_statuses == [("entry", "filled_live"), ("exit", "filled_sync")]
    assert requested_notional == 500.0
    assert position == ("closed", 101.0, 103.1)
    assert notifier.events == [("enter_long", "AAAUSDT"), ("take_profit_exit", "AAAUSDT")]


async def _exercise_live_break_even_stop_update(tmp_path: Path) -> None:
    class FakeLiveClient:
        def __init__(self) -> None:
            self.position_open = False
            self.stops: list[tuple[str, str, str]] = []

        def enabled(self) -> bool:
            return True

        async def get_wallet_balance(self):
            return type(
                "WalletBalance",
                (),
                {
                    "total_equity_usd": Decimal("1000"),
                    "total_available_balance_usd": Decimal("1000"),
                },
            )()

        async def fetch_instrument_spec(self, symbol: str) -> InstrumentSpec:
            return InstrumentSpec(
                symbol=symbol,
                qty_step=Decimal("0.001"),
                min_order_qty=Decimal("0.001"),
                tick_size=Decimal("0.1"),
            )

        async def place_market_order(
            self,
            *,
            symbol: str,
            side: str,
            quantity: Decimal,
            order_link_id: str,
            reduce_only: bool = False,
        ):
            return type("Ack", (), {"order_id": "demo-order-1", "order_link_id": order_link_id})()

        async def wait_for_position(self, symbol: str) -> VenuePosition:
            self.position_open = True
            return VenuePosition(
                symbol=symbol,
                side="Buy",
                size=Decimal("1.980"),
                avg_price=Decimal("101.0"),
                position_idx=0,
            )

        async def set_trading_stop(self, *, symbol: str, position_idx: int, take_profit: Decimal, stop_loss: Decimal) -> None:
            self.stops.append((symbol, format(take_profit, "f"), format(stop_loss, "f")))

        async def get_position(self, symbol: str):
            if self.position_open:
                return VenuePosition(
                    symbol=symbol,
                    side="Buy",
                    size=Decimal("1.980"),
                    avg_price=Decimal("101.0"),
                    position_idx=0,
                )
            return None

        async def get_latest_closed_pnl(self, symbol: str):
            return None

    settings = Settings(
        sqlite_path=str(tmp_path / "execution-live-break-even.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=True,
        risk_per_trade_pct=0.01,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
        break_even_stop_enabled=True,
        break_even_stop_trigger_fraction_of_tp=0.75,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    fake_client = FakeLiveClient()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        client=fake_client,
    )

    await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 102.6) is True
    await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 3,
        ranked_signals=[],
    )

    assert fake_client.stops == [
        ("AAAUSDT", "103.0", "99.0"),
        ("AAAUSDT", "103.0", "101.0"),
    ]
    with sqlite3.connect(settings.sqlite_path) as connection:
        armed_flag = connection.execute(
            "SELECT break_even_stop_active FROM positions WHERE ticker = 'AAAUSDT'"
        ).fetchone()[0]
    assert armed_flag == 1


async def _exercise_max_entries_per_rebalance(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-rebalance-cap.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        max_entries_per_rebalance=1,
    )
    state = MarketState(settings=settings)
    for symbol, price in (
        ("BTCUSDT", 20_000.0),
        ("AAAUSDT", 100.0),
        ("BBBUSDT", 90.0),
        ("CCCUSDT", 80.0),
    ):
        state.replace_history(
            symbol,
            [(0, price), (settings.ticker_interval_ms, price + 1.0)],
        )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            ),
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="BBBUSDT",
                current_price=91.0,
                momentum_z=1.5,
                curvature=0.15,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=2,
                persistence_hits=0,
                alerted=False,
            ),
        ],
    )

    assert [(action.action, action.ticker) for action in actions] == [
        ("enter_long", "AAAUSDT"),
        ("skip_entry", "BBBUSDT"),
    ]
    assert "max_entries_per_rebalance_reached" in actions[1].detail

    with sqlite3.connect(settings.sqlite_path) as connection:
        open_positions = connection.execute(
            "SELECT ticker FROM positions WHERE status = 'open' ORDER BY ticker"
        ).fetchall()
    assert open_positions == [("AAAUSDT",)]


async def _exercise_max_open_positions(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-open-cap.db"),
        universe=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        max_open_positions=1,
        max_entries_per_rebalance=0,
    )
    state = MarketState(settings=settings)
    for symbol, price in (
        ("BTCUSDT", 20_000.0),
        ("AAAUSDT", 100.0),
        ("BBBUSDT", 90.0),
        ("CCCUSDT", 80.0),
    ):
        state.replace_history(
            symbol,
            [(0, price), (settings.ticker_interval_ms, price + 1.0)],
        )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            ),
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="BBBUSDT",
                current_price=91.0,
                momentum_z=1.5,
                curvature=0.15,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=2,
                persistence_hits=0,
                alerted=False,
            ),
        ],
    )

    assert [(action.action, action.ticker) for action in actions] == [
        ("enter_long", "AAAUSDT"),
        ("skip_entry", "BBBUSDT"),
    ]
    assert "max_open_positions_reached" in actions[1].detail

    with sqlite3.connect(settings.sqlite_path) as connection:
        open_positions = connection.execute(
            "SELECT ticker FROM positions WHERE status = 'open' ORDER BY ticker"
        ).fetchall()
    assert open_positions == [("AAAUSDT",)]


async def _exercise_max_positions_per_cluster(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-cluster-cap.db"),
        universe=["ARBUSDT", "OPUSDT", "AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        max_open_positions=3,
        max_positions_per_cluster=1,
    )
    state = MarketState(settings=settings)
    for symbol, price in (
        ("BTCUSDT", 20_000.0),
        ("ARBUSDT", 1.0),
        ("OPUSDT", 2.0),
        ("AAAUSDT", 3.0),
    ):
        state.replace_history(
            symbol,
            [(0, price), (settings.ticker_interval_ms, price + 0.1)],
        )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="ARBUSDT",
                current_price=1.1,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            ),
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="OPUSDT",
                current_price=2.1,
                momentum_z=1.5,
                curvature=0.15,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=2,
                persistence_hits=0,
                alerted=False,
            ),
        ],
    )

    assert [(action.action, action.ticker) for action in actions] == [
        ("enter_long", "ARBUSDT"),
        ("skip_entry", "OPUSDT"),
    ]
    assert (
        "max_positions_per_cluster_reached cluster=manual:l2 count=1 limit=1"
        == actions[1].detail
    )


async def _exercise_daily_stop_loss_reset(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-daily-stop-reset.db"),
        universe=["AAAUSDT", "BBBUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        max_daily_stop_losses=1,
        take_profit_pct=0.02,
        stop_loss_pct=0.02,
    )
    state = MarketState(settings=settings)
    for symbol, price in (
        ("BTCUSDT", 20_000.0),
        ("AAAUSDT", 100.0),
        ("BBBUSDT", 90.0),
    ):
        state.replace_history(
            symbol,
            [(0, price), (settings.ticker_interval_ms, price + 1.0)],
        )

    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    current_time = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)

    def fake_now(*, cycle_time_ms, stage):
        return current_time

    execution._now = fake_now  # type: ignore[method-assign]

    first_entry = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in first_entry] == [
        ("enter_long", "AAAUSDT")
    ]

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 98.90) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    first_exit = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in first_exit] == [
        ("exit_long", "AAAUSDT")
    ]

    current_time = datetime(2026, 4, 9, 13, 0, tzinfo=timezone.utc)
    blocked_same_day = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 3,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="BBBUSDT",
                current_price=91.0,
                momentum_z=1.5,
                curvature=0.15,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in blocked_same_day] == [
        ("skip_entry", "BBBUSDT")
    ]
    assert "max_daily_stop_losses_reached" in blocked_same_day[0].detail

    current_time = datetime(2026, 4, 10, 0, 5, tzinfo=timezone.utc)
    allowed_next_day = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 4,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="BBBUSDT",
                current_price=91.0,
                momentum_z=1.5,
                curvature=0.15,
                hurst=0.68,
                regime_score=2,
                composite_score=1.2,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in allowed_next_day] == [
        ("enter_long", "BBBUSDT")
    ]


async def _exercise_operator_pause(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-operator-pause.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        operator_pause_new_entries=True,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )
    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(settings=settings, state=state, database=database)

    actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.1,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    assert [(action.action, action.ticker) for action in actions] == [("skip_entry", "AAAUSDT")]
    assert actions[0].detail == "operator_pause_new_entries"


async def _exercise_detect_position_drift(tmp_path: Path) -> None:
    class FakeLiveClient:
        def enabled(self) -> bool:
            return True

        async def get_position(self, symbol: str):
            if symbol == "AAAUSDT":
                return VenuePosition(
                    symbol=symbol,
                    side="Buy",
                    size=Decimal("1.000"),
                    avg_price=Decimal("100.0"),
                    position_idx=0,
                )
            return None

    settings = Settings(
        sqlite_path=str(tmp_path / "execution-drift.db"),
        universe=["AAAUSDT", "BBBUSDT"],
        execution_enabled=True,
        execution_submit_orders=True,
        demo_mode=True,
    )
    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    order_id = await database.create_order(
        client_order_id="entry-bbb",
        ticker="BBBUSDT",
        side="Buy",
        order_type="market",
        lifecycle="entry",
        status="filled_live",
        stage="emerging",
        signal_kind="entry_ready",
        requested_qty=1.0,
        requested_notional_usd=100.0,
        requested_price=100.0,
        venue="bybit",
        is_demo=True,
        created_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T00:00:00+00:00",
        fill_price=100.0,
        external_order_id="venue-1",
        filled_at="2026-04-11T00:00:01+00:00",
    )
    await database.open_position(
        ticker="BBBUSDT",
        side="LONG",
        opened_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T00:00:00+00:00",
        entry_order_id=order_id,
        entry_stage="emerging",
        entry_signal_kind="entry_ready",
        quantity=1.0,
        notional_usd=100.0,
        entry_price=100.0,
    )
    execution = ExecutionEngine(
        settings=settings,
        state=MarketState(settings=settings),
        database=database,
        client=FakeLiveClient(),
    )

    status, detail = await execution.detect_position_drift()

    assert status == "mismatch"
    assert "local_only=BBBUSDT" in detail
    assert "venue_only=AAAUSDT" in detail

async def _exercise_live_duplicate_position_guard(tmp_path: Path) -> None:
    class FakeLiveClient:
        def enabled(self) -> bool:
            return True

        async def get_position(self, symbol: str):
            return VenuePosition(
                symbol=symbol,
                side="Buy",
                size=Decimal("1.000"),
                avg_price=Decimal("100.0"),
                position_idx=0,
            )

    settings = Settings(
        sqlite_path=str(tmp_path / "execution-live-dup.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=True,
        entry_notional_usd=100.0,
    )
    state = MarketState(settings=settings)
    state.replace_history(
        "BTCUSDT",
        [(0, 20_000.0), (settings.ticker_interval_ms, 20_100.0)],
    )
    state.replace_history(
        "AAAUSDT",
        [(0, 100.0), (settings.ticker_interval_ms, 101.0)],
    )
    database = SignalDatabase(settings.sqlite_path)
    await database.initialize()
    execution = ExecutionEngine(
        settings=settings,
        state=state,
        database=database,
        client=FakeLiveClient(),
    )

    actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="emerging",
                signal_kind="entry_ready",
                ticker="AAAUSDT",
                current_price=101.0,
                momentum_z=2.0,
                curvature=0.2,
                hurst=0.7,
                regime_score=2,
                composite_score=1.4,
                rank=1,
                persistence_hits=0,
                alerted=False,
            )
        ],
    )

    assert [(action.action, action.ticker) for action in actions] == [("skip_entry", "AAAUSDT")]
    assert "venue_position_already_open" in actions[0].detail
