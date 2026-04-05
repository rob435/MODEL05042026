from __future__ import annotations

import argparse
import asyncio
import logging

import aiohttp

from config import Settings, load_settings


LOGGER = logging.getLogger(__name__)


def split_universe(configured_symbols: list[str], listed_symbols: set[str]) -> tuple[list[str], list[str]]:
    configured = sorted(set(configured_symbols))
    valid = [symbol for symbol in configured if symbol in listed_symbols]
    invalid = [symbol for symbol in configured if symbol not in listed_symbols]
    return valid, invalid


async def fetch_listed_symbols(session: aiohttp.ClientSession, settings: Settings) -> set[str]:
    listed_symbols: set[str] = set()
    cursor = ""

    while True:
        params: dict[str, str | int] = {
            "category": settings.bybit_category,
            "status": "Trading",
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor

        async with session.get(
            f"{settings.bybit_rest_base_url.rstrip('/')}/v5/market/instruments-info",
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            payload = await response.json()

        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit instruments error: {payload}")

        result = payload.get("result", {})
        for item in result.get("list", []):
            symbol = item.get("symbol")
            if symbol:
                listed_symbols.add(symbol)

        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            break

    return listed_symbols


async def validate_universe(settings: Settings) -> tuple[list[str], list[str]]:
    async with aiohttp.ClientSession() as session:
        listed_symbols = await fetch_listed_symbols(session, settings)
    return split_universe(settings.tracked_symbols, listed_symbols)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate configured universe symbols against Bybit instruments.")
    return parser.parse_args()


def main() -> None:
    parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    valid, invalid = asyncio.run(validate_universe(settings))
    LOGGER.info("Validated %s configured symbols against Bybit %s instruments", len(valid) + len(invalid), settings.bybit_category)
    if valid:
        LOGGER.info("Valid symbols: %s", ", ".join(valid))
    if invalid:
        LOGGER.warning("Invalid or unavailable symbols: %s", ", ".join(invalid))
    else:
        LOGGER.info("No invalid symbols detected")


if __name__ == "__main__":
    main()
