import asyncio
import sqlite3
from decimal import Decimal
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


def test_execution_engine_enters_confirms_and_takes_profit_with_telegram(tmp_path: Path) -> None:
    asyncio.run(_exercise_execution_take_profit_lifecycle(tmp_path))


def test_execution_engine_stops_out_with_telegram(tmp_path: Path) -> None:
    asyncio.run(_exercise_stop_loss_lifecycle(tmp_path))


def test_execution_engine_allows_multiple_tickers_but_only_one_position_per_ticker(tmp_path: Path) -> None:
    asyncio.run(_exercise_per_ticker_entry_guard(tmp_path))


def test_execution_engine_limits_entries_per_rebalance(tmp_path: Path) -> None:
    asyncio.run(_exercise_max_entries_per_rebalance(tmp_path))


def test_execution_engine_respects_max_open_positions(tmp_path: Path) -> None:
    asyncio.run(_exercise_max_open_positions(tmp_path))


def test_execution_engine_places_real_demo_order_and_syncs_exchange_exit(tmp_path: Path) -> None:
    asyncio.run(_exercise_live_demo_path(tmp_path))


async def _exercise_execution_take_profit_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution.db"),
        universe=["AAAUSDT", "BBBUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.03,
        stop_loss_pct=0.02,
        exit_on_lost_confirmed=False,
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
    assert [(action.action, action.ticker) for action in entry_actions] == [("enter_short", "AAAUSDT")]

    confirm_actions = await execution.process_cycle(
        stage="confirmed",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[
            RankedSignal(
                stage="confirmed",
                signal_kind="confirmed",
                ticker="AAAUSDT",
                current_price=102.0,
                momentum_z=2.1,
                curvature=0.2,
                hurst=0.72,
                regime_score=2,
                composite_score=1.5,
                rank=1,
                persistence_hits=1,
                alerted=False,
            )
        ],
    )
    assert [(action.action, action.ticker) for action in confirm_actions] == [
        ("confirm_position", "AAAUSDT")
    ]

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 97.50) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in exit_actions] == [
        ("exit_short", "AAAUSDT")
    ]

    with sqlite3.connect(settings.sqlite_path) as connection:
        order_counts = dict(
            connection.execute(
                "SELECT lifecycle, COUNT(*) FROM orders GROUP BY lifecycle"
            ).fetchall()
        )
        position = connection.execute(
            """
            SELECT status, confirmation_signal_kind, entry_price, exit_price
            FROM positions
            WHERE ticker = 'AAAUSDT'
            """
        ).fetchone()

    assert order_counts == {"entry": 1, "exit": 1}
    assert position == ("closed", "confirmed", 101.0, 97.5)
    assert notifier.events == [("enter_short", "AAAUSDT"), ("take_profit_exit", "AAAUSDT")]


async def _exercise_stop_loss_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "execution-stop.db"),
        universe=["AAAUSDT"],
        execution_enabled=True,
        demo_mode=True,
        execution_submit_orders=False,
        entry_notional_usd=200.0,
        take_profit_pct=0.03,
        stop_loss_pct=0.02,
        exit_on_lost_confirmed=False,
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

    assert state.update_provisional("AAAUSDT", settings.ticker_interval_ms * 2, 103.10) is True
    assert state.update_provisional("BTCUSDT", settings.ticker_interval_ms * 2, 20_200.0) is True

    exit_actions = await execution.process_cycle(
        stage="emerging",
        cycle_time_ms=settings.ticker_interval_ms * 2,
        ranked_signals=[],
    )
    assert [(action.action, action.ticker) for action in exit_actions] == [
        ("exit_short", "AAAUSDT")
    ]
    assert notifier.events == [("enter_short", "AAAUSDT"), ("stop_loss_exit", "AAAUSDT")]

    with sqlite3.connect(settings.sqlite_path) as connection:
        position = connection.execute(
            "SELECT status, exit_price FROM positions WHERE ticker = 'AAAUSDT'"
        ).fetchone()
    assert position == ("closed", 103.1)


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
        ("enter_short", "AAAUSDT"),
        ("enter_short", "BBBUSDT"),
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

        async def place_market_order(self, *, symbol: str, side: str, quantity: Decimal, order_link_id: str):
            self.last_quantity = quantity
            return type("Ack", (), {"order_id": "demo-order-1", "order_link_id": order_link_id})()

        async def wait_for_position(self, symbol: str) -> VenuePosition:
            self.position_open = True
            return VenuePosition(
                symbol=symbol,
                side="Sell",
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
                    side="Sell",
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
                avg_exit_price=Decimal("97.9"),
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
        take_profit_pct=0.03,
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
    assert [(action.action, action.ticker) for action in entry_actions] == [("enter_short", "AAAUSDT")]
    assert fake_client.last_quantity == Decimal("4.950")
    assert fake_client.stops == [("AAAUSDT", "98.0", "103.0")]

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
    assert position == ("closed", 101.0, 97.9)
    assert notifier.events == [("enter_short", "AAAUSDT"), ("take_profit_exit", "AAAUSDT")]


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
        ("enter_short", "AAAUSDT"),
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
        ("enter_short", "AAAUSDT"),
        ("skip_entry", "BBBUSDT"),
    ]
    assert "max_open_positions_reached" in actions[1].detail

    with sqlite3.connect(settings.sqlite_path) as connection:
        open_positions = connection.execute(
            "SELECT ticker FROM positions WHERE status = 'open' ORDER BY ticker"
        ).fetchall()
    assert open_positions == [("AAAUSDT",)]


def test_execution_engine_skips_live_entry_when_venue_position_exists(tmp_path: Path) -> None:
    asyncio.run(_exercise_live_duplicate_position_guard(tmp_path))


async def _exercise_live_duplicate_position_guard(tmp_path: Path) -> None:
    class FakeLiveClient:
        def enabled(self) -> bool:
            return True

        async def get_position(self, symbol: str):
            return VenuePosition(
                symbol=symbol,
                side="Sell",
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
