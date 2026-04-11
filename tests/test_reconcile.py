from __future__ import annotations

import asyncio
from pathlib import Path

from database import SignalDatabase
from reconcile import (
    export_reconciliation,
    load_actual_trade_events_from_db,
    load_backtest_trade_events,
    log_reconciliation_result,
    parse_telegram_trade_events,
    reconcile_trade_events,
)


def test_parse_telegram_trade_events_extracts_entries_and_exits() -> None:
    html = """
    <div class="message default clearfix" id="message1">
      <div class="body">
        <div class="pull_right date details" title="05.04.2026 22:32:07 UTC+00:00">22:32</div>
        <div class="from_name">BybitDemo_bot </div>
        <div class="text">SOLUSDT entered new long position<br>Stage: EMERGING</div>
      </div>
    </div>
    <div class="message default clearfix joined" id="message2">
      <div class="body">
        <div class="pull_right date details" title="05.04.2026 22:40:07 UTC+00:00">22:40</div>
        <div class="text">SOLUSDT take-profit exit<br>Stage: EMERGING</div>
      </div>
    </div>
    """
    events = parse_telegram_trade_events(html)
    assert [(event.ticker, event.event, event.side) for event in events] == [
        ("SOLUSDT", "entry", "LONG"),
        ("SOLUSDT", "exit", "LONG"),
    ]
    assert events[1].reason == "take-profit exit"


def test_reconcile_trade_events_matches_by_ticker_and_time(tmp_path: Path) -> None:
    csv_path = tmp_path / "backtest_trades.csv"
    csv_path.write_text(
        "ticker,side,opened_at,closed_at\n"
        "SOLUSDT,LONG,2026-04-05T22:32:30+00:00,2026-04-05T22:39:50+00:00\n",
        encoding="utf-8",
    )
    actual_html = """
    <div class="message default clearfix" id="message1">
      <div class="body">
        <div class="pull_right date details" title="05.04.2026 22:32:07 UTC+00:00">22:32</div>
        <div class="from_name">BybitDemo_bot </div>
        <div class="text">SOLUSDT entered new long position<br>Stage: EMERGING</div>
      </div>
    </div>
    <div class="message default clearfix joined" id="message2">
      <div class="body">
        <div class="pull_right date details" title="05.04.2026 22:40:07 UTC+00:00">22:40</div>
        <div class="text">SOLUSDT venue-managed exit<br>Stage: EMERGING</div>
      </div>
    </div>
    """
    actual = parse_telegram_trade_events(actual_html)
    backtest = load_backtest_trade_events(str(csv_path))
    result = reconcile_trade_events(actual, backtest, tolerance_minutes=1)
    assert result.summary.matched_entries == 1
    assert result.summary.matched_exits == 1
    assert result.summary.entry_precision == 1.0
    assert result.summary.exit_recall == 1.0
    assert result.summary.matched_exit_reason_count == 0
    assert result.summary.actual_unique_tickers == 1
    assert result.summary.backtest_unique_tickers == 1
    assert result.summary.matched_unique_tickers == 1
    assert result.unmatched_actual_entries == []
    assert result.unmatched_backtest_entries == []
    assert len(result.tickers) == 1
    assert result.tickers[0].ticker == "SOLUSDT"
    assert result.tickers[0].matched_entries == 1
    assert result.tickers[0].matched_exits == 1


def test_reconciliation_exports_ticker_summary(tmp_path: Path) -> None:
    actual_html = """
    <div class="message default clearfix" id="message1">
      <div class="body">
        <div class="pull_right date details" title="05.04.2026 22:32:07 UTC+00:00">22:32</div>
        <div class="from_name">BybitDemo_bot </div>
        <div class="text">SOLUSDT entered new long position<br>Stage: EMERGING</div>
      </div>
    </div>
    """
    csv_path = tmp_path / "backtest_trades.csv"
    csv_path.write_text(
        "ticker,side,opened_at,closed_at\n"
        "SOLUSDT,LONG,2026-04-05T22:32:30+00:00,2026-04-05T22:39:50+00:00\n",
        encoding="utf-8",
    )
    result = reconcile_trade_events(
        parse_telegram_trade_events(actual_html),
        load_backtest_trade_events(str(csv_path)),
        tolerance_minutes=1,
    )
    export_reconciliation(result, export_dir=str(tmp_path / "reconciliation"))
    ticker_csv = (tmp_path / "reconciliation" / "ticker_reconciliation.csv").read_text(encoding="utf-8")
    assert "ticker,actual_entries,backtest_entries,matched_entries" in ticker_csv
    assert "SOLUSDT,1,1,1" in ticker_csv


def test_load_actual_trade_events_from_db_filters_one_utc_day(tmp_path: Path) -> None:
    asyncio.run(_exercise_load_actual_trade_events_from_db(tmp_path))


def test_log_reconciliation_result_persists_summary_row(tmp_path: Path) -> None:
    asyncio.run(_exercise_log_reconciliation_result(tmp_path))


async def _exercise_load_actual_trade_events_from_db(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    database = SignalDatabase(str(db_path))
    await database.initialize()
    database._conn.execute(
        """
        INSERT INTO trade_analytics (
            position_id, ticker, side, entry_stage, entry_signal_kind, exit_stage, exit_signal_kind,
            exit_event, exit_reason, opened_at, closed_at, holding_minutes, bars_held, quantity,
            notional_usd, entry_price, exit_price, realized_pnl_pct, realized_pnl_usd, mfe_pct, mae_pct,
            notes, post_exit_bars_target, post_exit_bars_observed, post_exit_completed,
            post_exit_favorable_excursion_pct, post_exit_adverse_excursion_pct
        ) VALUES (
            1, 'SOLUSDT', 'LONG', 'emerging', 'entry_ready', 'emerging', 'exit', 'take_profit', 'take_profit',
            '2026-04-11T01:00:00+00:00', '2026-04-11T02:00:00+00:00', 60, 4, 1, 100, 100, 102, 0.02, 2.0, 0.02, 0.01,
            '', 0, 0, 0, 0, 0
        )
        """
    )
    database._conn.execute(
        """
        INSERT INTO trade_analytics (
            position_id, ticker, side, entry_stage, entry_signal_kind, exit_stage, exit_signal_kind,
            exit_event, exit_reason, opened_at, closed_at, holding_minutes, bars_held, quantity,
            notional_usd, entry_price, exit_price, realized_pnl_pct, realized_pnl_usd, mfe_pct, mae_pct,
            notes, post_exit_bars_target, post_exit_bars_observed, post_exit_completed,
            post_exit_favorable_excursion_pct, post_exit_adverse_excursion_pct
        ) VALUES (
            2, 'ADAUSDT', 'LONG', 'emerging', 'entry_ready', 'emerging', 'exit', 'stop_loss', 'stop_loss',
            '2026-04-10T23:00:00+00:00', '2026-04-11T00:15:00+00:00', 75, 5, 1, 100, 100, 98, -0.02, -2.0, 0.01, 0.02,
            '', 0, 0, 0, 0, 0
        )
        """
    )
    database._conn.commit()
    database.close()

    events = load_actual_trade_events_from_db(str(db_path), trade_date="2026-04-11")
    assert [(event.ticker, event.event) for event in events] == [
        ("ADAUSDT", "exit"),
        ("SOLUSDT", "entry"),
        ("SOLUSDT", "exit"),
    ]


async def _exercise_log_reconciliation_result(tmp_path: Path) -> None:
    actual_html = """
    <div class="message default clearfix" id="message1">
      <div class="body">
        <div class="pull_right date details" title="05.04.2026 22:32:07 UTC+00:00">22:32</div>
        <div class="from_name">BybitDemo_bot </div>
        <div class="text">SOLUSDT entered new long position<br>Stage: EMERGING</div>
      </div>
    </div>
    """
    csv_path = tmp_path / "backtest_trades.csv"
    csv_path.write_text(
        "ticker,side,opened_at,closed_at\n"
        "SOLUSDT,LONG,2026-04-05T22:32:30+00:00,2026-04-05T22:39:50+00:00\n",
        encoding="utf-8",
    )
    result = reconcile_trade_events(
        parse_telegram_trade_events(actual_html),
        load_backtest_trade_events(str(csv_path)),
        tolerance_minutes=1,
    )
    db_path = tmp_path / "signals.sqlite3"
    row_id = await log_reconciliation_result(
        db_path=str(db_path),
        result=result,
        window_date="2026-04-05",
        actual_source="telegram.html",
        backtest_source=str(csv_path),
        export_dir=str(tmp_path / "reconciliation"),
    )
    database = SignalDatabase(str(db_path))
    await database.initialize()
    row = database._conn.execute(
        "SELECT * FROM reconciliation_runs WHERE id = ?",
        (row_id,),
    ).fetchone()
    database.close()
    assert row is not None
    assert row["window_date"] == "2026-04-05"
    assert row["matched_entries"] == 1
    assert row["matched_unique_tickers"] == 1
