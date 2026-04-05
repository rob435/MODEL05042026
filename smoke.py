from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from config import load_settings
from replay import run_replay
from report import format_report, load_report_summary
from universe_validator import validate_universe


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SmokeResult:
    valid_symbols: list[str]
    invalid_symbols: list[str]
    replay_db_path: str


def should_fail_smoke(invalid_symbols: list[str], strict_universe: bool) -> bool:
    return bool(strict_universe and invalid_symbols)


async def run_smoke(cycles: int, replay_db_path: str, strict_universe: bool) -> SmokeResult:
    settings = load_settings()
    valid_symbols, invalid_symbols = await validate_universe(settings)
    if should_fail_smoke(invalid_symbols, strict_universe):
        raise RuntimeError(f"Universe validation failed: {', '.join(invalid_symbols)}")

    await run_replay(
        settings=settings,
        replay_cycles=cycles,
        sqlite_path=replay_db_path,
        enable_telegram=False,
    )
    return SmokeResult(
        valid_symbols=valid_symbols,
        invalid_symbols=invalid_symbols,
        replay_db_path=replay_db_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live smoke validation against Bybit.")
    parser.add_argument("--cycles", type=int, default=4, help="Number of replay cycles to run.")
    parser.add_argument("--db", type=str, default="smoke-replay.sqlite3", help="Replay SQLite output path.")
    parser.add_argument(
        "--strict-universe",
        action="store_true",
        help="Fail immediately if any configured symbols are not listed by Bybit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    replay_db_path = str(Path(args.db).expanduser())
    result = asyncio.run(
        run_smoke(
            cycles=args.cycles,
            replay_db_path=replay_db_path,
            strict_universe=args.strict_universe,
        )
    )
    LOGGER.info("Valid symbols: %s", len(result.valid_symbols))
    if result.invalid_symbols:
        LOGGER.warning("Invalid symbols: %s", ", ".join(result.invalid_symbols))
    summary = load_report_summary(result.replay_db_path, top_n=5)
    print(format_report(summary))


if __name__ == "__main__":
    main()
