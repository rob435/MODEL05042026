from __future__ import annotations

import argparse
from pathlib import Path


SAFE_LOCAL_OVERRIDES = {
    "EXECUTION_ENABLED": "false",
    "EXECUTION_SUBMIT_ORDERS": "false",
    "SQLITE_PATH": "signals.sqlite3",
    "BACKTEST_CACHE_PATH": ".cache/backtest_candles.sqlite3",
    "TELEGRAM_SIGNAL_ALERTS_ENABLED": "false",
    "WATCHLIST_TELEGRAM_ENABLED": "false",
    "BYBIT_API_KEY": "",
    "BYBIT_API_SECRET": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
}


def localize_env_text(template_text: str, overrides: dict[str, str] | None = None) -> str:
    active_overrides = {**SAFE_LOCAL_OVERRIDES, **(overrides or {})}
    output_lines: list[str] = []
    seen_keys: set[str] = set()

    for line in template_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output_lines.append(line)
            continue

        key, _value = line.split("=", 1)
        key = key.strip()
        if key in active_overrides:
            output_lines.append(f"{key}={active_overrides[key]}")
            seen_keys.add(key)
        else:
            output_lines.append(line)

    for key, value in active_overrides.items():
        if key not in seen_keys:
            output_lines.append(f"{key}={value}")

    return "\n".join(output_lines) + "\n"


def write_local_env(
    template_path: Path,
    output_path: Path,
    sqlite_path: str,
    cache_path: str,
    force: bool = False,
) -> Path:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists. Use --force to overwrite it.")

    template_text = template_path.read_text(encoding="utf-8")
    localized = localize_env_text(
        template_text,
        overrides={
            "SQLITE_PATH": sqlite_path,
            "BACKTEST_CACHE_PATH": cache_path,
        },
    )
    output_path.write_text(localized, encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a safe local-research .env from the production template."
    )
    parser.add_argument(
        "--template",
        default="deploy/production.env.example",
        help="Path to the source env template.",
    )
    parser.add_argument(
        "--output",
        default=".env",
        help="Path to write the localized env file.",
    )
    parser.add_argument(
        "--sqlite-path",
        default="signals.sqlite3",
        help="Local SQLite path to write into the output env.",
    )
    parser.add_argument(
        "--cache-path",
        default=".cache/backtest_candles.sqlite3",
        help="Local backtest cache path to write into the output env.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    output_path = write_local_env(
        template_path=Path(args.template),
        output_path=Path(args.output),
        sqlite_path=args.sqlite_path,
        cache_path=args.cache_path,
        force=args.force,
    )
    print(f"Wrote localized research env to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
