from __future__ import annotations

from pathlib import Path

from reconcile import load_backtest_trade_events, parse_telegram_trade_events, reconcile_trade_events


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
    assert result.unmatched_actual_entries == []
    assert result.unmatched_backtest_entries == []
