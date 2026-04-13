from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

from deploy.cache_bundle import inspect_cache, pack_cache, sqlite_row_count, unpack_cache
from deploy.prepare_local_env import localize_env_text, write_local_env


def _create_cache(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            create table historical_candles (
                symbol text not null,
                interval text not null,
                open_time integer not null,
                close real not null
            )
            """
        )
        connection.executemany(
            "insert into historical_candles(symbol, interval, open_time, close) values (?, ?, ?, ?)",
            [
                ("BTCUSDT", "1", 1, 100.0),
                ("BTCUSDT", "1", 2, 101.0),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_localize_env_text_applies_safe_research_defaults() -> None:
    template = "\n".join(
        [
            "EXECUTION_ENABLED=true",
            "EXECUTION_SUBMIT_ORDERS=true",
            "SQLITE_PATH=/opt/MODEL05042026/data/signals.sqlite3",
            "BACKTEST_CACHE_PATH=/opt/MODEL05042026/data/backtest-candles.sqlite3",
            "TELEGRAM_SIGNAL_ALERTS_ENABLED=true",
            "BYBIT_API_KEY=REPLACE_WITH_DEMO_KEY",
        ]
    )
    localized = localize_env_text(template)
    assert "EXECUTION_ENABLED=false" in localized
    assert "EXECUTION_SUBMIT_ORDERS=false" in localized
    assert "SQLITE_PATH=signals.sqlite3" in localized
    assert "BACKTEST_CACHE_PATH=.cache/backtest_candles.sqlite3" in localized
    assert "TELEGRAM_SIGNAL_ALERTS_ENABLED=false" in localized
    assert "BYBIT_API_KEY=" in localized


def test_write_local_env_respects_custom_paths(tmp_path: Path) -> None:
    template = tmp_path / "production.env.example"
    template.write_text(
        "SQLITE_PATH=/opt/MODEL05042026/data/signals.sqlite3\n"
        "BACKTEST_CACHE_PATH=/opt/MODEL05042026/data/backtest-candles.sqlite3\n",
        encoding="utf-8",
    )
    output = tmp_path / ".env"
    write_local_env(template, output, sqlite_path="local-signals.sqlite3", cache_path="cache/db.sqlite3")
    text = output.read_text(encoding="utf-8")
    assert "SQLITE_PATH=local-signals.sqlite3" in text
    assert "BACKTEST_CACHE_PATH=cache/db.sqlite3" in text


def test_pack_and_unpack_cache_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "backtest.sqlite3"
    _create_cache(source)
    archive = tmp_path / "cache.sqlite3.gz"
    restored = tmp_path / "restored.sqlite3"

    pack_cache(source, archive)
    assert archive.exists()
    with gzip.open(archive, "rb") as handle:
        assert handle.read(16)

    unpack_cache(archive, restored)
    assert sqlite_row_count(restored) == 2
    info = inspect_cache(restored)
    assert "historical_candles=2" in info
