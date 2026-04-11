from __future__ import annotations

import argparse
import csv
import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


MESSAGE_RE = re.compile(
    r'<div class="message default clearfix(?: joined)?".*?'
    r'<div class="pull_right date details" title="(?P<title>[^"]+)">.*?</div>'
    r'(?:(?:.|\n)*?<div class="from_name">\s*(?P<sender>[^<]+)\s*</div>)?'
    r'(?:(?:.|\n)*?<div class="text">(?P<text>.*?)</div>)',
    re.S,
)


@dataclass(slots=True)
class ActualTradeEvent:
    timestamp: str
    ticker: str
    event: str
    side: str
    reason: str | None = None


@dataclass(slots=True)
class MatchedTradeEvent:
    ticker: str
    event: str
    side: str
    actual_timestamp: str
    backtest_timestamp: str
    delta_seconds: float
    actual_reason: str | None = None
    backtest_reason: str | None = None
    reason_match: bool | None = None


@dataclass(slots=True)
class ReconciliationSummary:
    actual_entries: int
    backtest_entries: int
    matched_entries: int
    entry_precision: float
    entry_recall: float
    avg_entry_delta_seconds: float | None
    actual_exits: int
    backtest_exits: int
    matched_exits: int
    exit_precision: float
    exit_recall: float
    avg_exit_delta_seconds: float | None
    matched_exit_reason_count: int
    mismatched_exit_reason_count: int


@dataclass(slots=True)
class ReconciliationResult:
    summary: ReconciliationSummary
    matched_entries: list[MatchedTradeEvent]
    matched_exits: list[MatchedTradeEvent]
    unmatched_actual_entries: list[ActualTradeEvent]
    unmatched_backtest_entries: list[ActualTradeEvent]
    unmatched_actual_exits: list[ActualTradeEvent]
    unmatched_backtest_exits: list[ActualTradeEvent]


def _clean_html_text(raw: str) -> str:
    text = raw.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_telegram_trade_events(html_text: str) -> list[ActualTradeEvent]:
    events: list[ActualTradeEvent] = []
    last_sender: str | None = None
    for match in MESSAGE_RE.finditer(html_text):
        sender = (match.group("sender") or last_sender or "").strip()
        if sender:
            last_sender = sender
        if "BybitDemo_bot" not in sender:
            continue
        text = _clean_html_text(match.group("text") or "")
        title = match.group("title")
        if not text or not title:
            continue
        timestamp = datetime.strptime(title, "%d.%m.%Y %H:%M:%S UTC+00:00").isoformat() + "+00:00"
        entry_match = re.match(r"(?P<ticker>[A-Z0-9]+USDT) entered new (?P<side>long|short) position", text)
        if entry_match:
            events.append(
                ActualTradeEvent(
                    timestamp=timestamp,
                    ticker=entry_match.group("ticker"),
                    event="entry",
                    side=entry_match.group("side").upper(),
                    reason="entry",
                )
            )
            continue
        exit_match = re.match(
            r"(?P<ticker>[A-Z0-9]+USDT) (?P<label>take-profit exit|stop-loss exit|venue-managed exit|exchange exit)",
            text,
        )
        if exit_match:
            events.append(
                ActualTradeEvent(
                    timestamp=timestamp,
                    ticker=exit_match.group("ticker"),
                    event="exit",
                    side="LONG",
                    reason=exit_match.group("label"),
                )
            )
    return events


def load_backtest_trade_events(csv_path: str) -> list[ActualTradeEvent]:
    events: list[ActualTradeEvent] = []
    with Path(csv_path).expanduser().open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticker = str(row.get("ticker", "")).strip()
            side = str(row.get("side", "LONG")).strip().upper()
            opened_at = str(row.get("opened_at", "")).strip()
            closed_at = str(row.get("closed_at", "")).strip()
            if ticker and opened_at:
                events.append(
                    ActualTradeEvent(
                        timestamp=opened_at,
                        ticker=ticker,
                        event="entry",
                        side=side,
                        reason="entry",
                    )
                )
            if ticker and closed_at:
                events.append(
                    ActualTradeEvent(
                        timestamp=closed_at,
                        ticker=ticker,
                        event="exit",
                        side=side,
                        reason=str(row.get("exit_reason", "")).strip() or None,
                    )
                )
    return events


def _match_events(
    actual: list[ActualTradeEvent],
    backtest: list[ActualTradeEvent],
    *,
    tolerance_minutes: int,
) -> tuple[list[MatchedTradeEvent], list[ActualTradeEvent], list[ActualTradeEvent]]:
    tolerance_seconds = tolerance_minutes * 60
    used_backtest: set[int] = set()
    matched: list[MatchedTradeEvent] = []
    unmatched_actual: list[ActualTradeEvent] = []
    for event in actual:
        event_dt = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
        best_index: int | None = None
        best_delta: float | None = None
        for index, candidate in enumerate(backtest):
            if index in used_backtest:
                continue
            if (
                candidate.ticker != event.ticker
                or candidate.event != event.event
                or candidate.side != event.side
            ):
                continue
            candidate_dt = datetime.fromisoformat(candidate.timestamp.replace("Z", "+00:00"))
            delta = abs((candidate_dt - event_dt).total_seconds())
            if delta > tolerance_seconds:
                continue
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_index = index
        if best_index is None:
            unmatched_actual.append(event)
            continue
        used_backtest.add(best_index)
        candidate = backtest[best_index]
        matched.append(
            MatchedTradeEvent(
                ticker=event.ticker,
                event=event.event,
                side=event.side,
                actual_timestamp=event.timestamp,
                backtest_timestamp=candidate.timestamp,
                delta_seconds=float(best_delta or 0.0),
                actual_reason=event.reason,
                backtest_reason=candidate.reason,
                reason_match=(
                    None
                    if event.event != "exit"
                    else event.reason == candidate.reason
                ),
            )
        )
    unmatched_backtest = [event for index, event in enumerate(backtest) if index not in used_backtest]
    return matched, unmatched_actual, unmatched_backtest


def reconcile_trade_events(
    actual_events: list[ActualTradeEvent],
    backtest_events: list[ActualTradeEvent],
    *,
    tolerance_minutes: int = 30,
) -> ReconciliationResult:
    actual_entries = [event for event in actual_events if event.event == "entry"]
    backtest_entries = [event for event in backtest_events if event.event == "entry"]
    actual_exits = [event for event in actual_events if event.event == "exit"]
    backtest_exits = [event for event in backtest_events if event.event == "exit"]
    matched_entries, unmatched_actual_entries, unmatched_backtest_entries = _match_events(
        actual_entries,
        backtest_entries,
        tolerance_minutes=tolerance_minutes,
    )
    matched_exits, unmatched_actual_exits, unmatched_backtest_exits = _match_events(
        actual_exits,
        backtest_exits,
        tolerance_minutes=tolerance_minutes,
    )
    matched_exit_reason_count = sum(1 for row in matched_exits if row.reason_match is True)
    mismatched_exit_reason_count = sum(1 for row in matched_exits if row.reason_match is False)
    return ReconciliationResult(
        summary=ReconciliationSummary(
            actual_entries=len(actual_entries),
            backtest_entries=len(backtest_entries),
            matched_entries=len(matched_entries),
            entry_precision=(len(matched_entries) / len(backtest_entries)) if backtest_entries else 0.0,
            entry_recall=(len(matched_entries) / len(actual_entries)) if actual_entries else 0.0,
            avg_entry_delta_seconds=(
                sum(row.delta_seconds for row in matched_entries) / len(matched_entries)
                if matched_entries
                else None
            ),
            actual_exits=len(actual_exits),
            backtest_exits=len(backtest_exits),
            matched_exits=len(matched_exits),
            exit_precision=(len(matched_exits) / len(backtest_exits)) if backtest_exits else 0.0,
            exit_recall=(len(matched_exits) / len(actual_exits)) if actual_exits else 0.0,
            avg_exit_delta_seconds=(
                sum(row.delta_seconds for row in matched_exits) / len(matched_exits)
                if matched_exits
                else None
            ),
            matched_exit_reason_count=matched_exit_reason_count,
            mismatched_exit_reason_count=mismatched_exit_reason_count,
        ),
        matched_entries=matched_entries,
        matched_exits=matched_exits,
        unmatched_actual_entries=unmatched_actual_entries,
        unmatched_backtest_entries=unmatched_backtest_entries,
        unmatched_actual_exits=unmatched_actual_exits,
        unmatched_backtest_exits=unmatched_backtest_exits,
    )


def format_reconciliation(result: ReconciliationResult) -> str:
    summary = result.summary
    lines = [
        "Backtest reconciliation",
        (
            f"  entries: actual={summary.actual_entries} backtest={summary.backtest_entries} "
            f"matched={summary.matched_entries} precision={summary.entry_precision * 100:.1f}% "
            f"recall={summary.entry_recall * 100:.1f}% "
            f"avg_delta_s={summary.avg_entry_delta_seconds:.1f}"
            if summary.avg_entry_delta_seconds is not None
            else (
                f"  entries: actual={summary.actual_entries} backtest={summary.backtest_entries} "
                f"matched={summary.matched_entries} precision={summary.entry_precision * 100:.1f}% "
                f"recall={summary.entry_recall * 100:.1f}% avg_delta_s=n/a"
            )
        ),
        (
            f"  exits: actual={summary.actual_exits} backtest={summary.backtest_exits} "
            f"matched={summary.matched_exits} precision={summary.exit_precision * 100:.1f}% "
            f"recall={summary.exit_recall * 100:.1f}% "
            f"avg_delta_s={summary.avg_exit_delta_seconds:.1f} "
            f"reason_match={summary.matched_exit_reason_count} "
            f"reason_mismatch={summary.mismatched_exit_reason_count}"
            if summary.avg_exit_delta_seconds is not None
            else (
                f"  exits: actual={summary.actual_exits} backtest={summary.backtest_exits} "
                f"matched={summary.matched_exits} precision={summary.exit_precision * 100:.1f}% "
                f"recall={summary.exit_recall * 100:.1f}% avg_delta_s=n/a "
                f"reason_match={summary.matched_exit_reason_count} "
                f"reason_mismatch={summary.mismatched_exit_reason_count}"
            )
        ),
    ]
    matched_sections = [
        ("Matched entries", result.matched_entries),
        ("Matched exits", result.matched_exits),
    ]
    for title, rows in matched_sections:
        if not rows:
            continue
        lines.append("")
        lines.append(title)
        for row in rows[:10]:
            reason_suffix = ""
            if row.event == "exit":
                reason_suffix = (
                    f" actual_reason={row.actual_reason} backtest_reason={row.backtest_reason}"
                )
            lines.append(
                f"  {row.ticker} {row.event} side={row.side} delta_s={row.delta_seconds:.1f}"
                f"{reason_suffix}"
            )
    sections = [
        ("Unmatched actual entries", result.unmatched_actual_entries),
        ("Unmatched backtest entries", result.unmatched_backtest_entries),
        ("Unmatched actual exits", result.unmatched_actual_exits),
        ("Unmatched backtest exits", result.unmatched_backtest_exits),
    ]
    for title, rows in sections:
        if not rows:
            continue
        lines.append("")
        lines.append(title)
        for row in rows[:20]:
            reason_suffix = f" reason={row.reason}" if row.reason else ""
            lines.append(f"  {row.timestamp} {row.ticker} {row.event} side={row.side}{reason_suffix}")
    return "\n".join(lines)


def export_reconciliation(result: ReconciliationResult, *, export_dir: str) -> None:
    export_path = Path(export_dir).expanduser()
    export_path.mkdir(parents=True, exist_ok=True)
    with (export_path / "reconciliation_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(result.summary)))
        writer.writeheader()
        writer.writerow(asdict(result.summary))
    matched_sections = {
        "matched_entries.csv": result.matched_entries,
        "matched_exits.csv": result.matched_exits,
    }
    for filename, rows in matched_sections.items():
        with (export_path / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "ticker",
                    "event",
                    "side",
                    "actual_timestamp",
                    "backtest_timestamp",
                    "delta_seconds",
                    "actual_reason",
                    "backtest_reason",
                    "reason_match",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))
    sections = {
        "unmatched_actual_entries.csv": result.unmatched_actual_entries,
        "unmatched_backtest_entries.csv": result.unmatched_backtest_entries,
        "unmatched_actual_exits.csv": result.unmatched_actual_exits,
        "unmatched_backtest_exits.csv": result.unmatched_backtest_exits,
    }
    for filename, rows in sections.items():
        with (export_path / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp", "ticker", "event", "side", "reason"])
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare actual Telegram trade events against backtest trade exports.")
    parser.add_argument("--telegram-html", required=True, help="Path to the Telegram export HTML file.")
    parser.add_argument("--backtest-trades-csv", required=True, help="Path to backtest_trades.csv.")
    parser.add_argument("--tolerance-minutes", type=int, default=30, help="Timestamp match tolerance in minutes.")
    parser.add_argument("--export-dir", default=None, help="Optional export directory for reconciliation CSVs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    telegram_html = Path(args.telegram_html).expanduser().read_text(encoding="utf-8")
    actual = parse_telegram_trade_events(telegram_html)
    backtest = load_backtest_trade_events(args.backtest_trades_csv)
    result = reconcile_trade_events(actual, backtest, tolerance_minutes=args.tolerance_minutes)
    if args.export_dir:
        export_reconciliation(result, export_dir=args.export_dir)
    print(format_reconciliation(result))


if __name__ == "__main__":
    main()
