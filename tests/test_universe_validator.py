from __future__ import annotations

from universe_validator import split_universe


def test_split_universe_separates_valid_and_invalid_symbols() -> None:
    valid, invalid = split_universe(
        configured_symbols=["BTCUSDT", "AAAUSDT", "BBBUSDt".upper(), "AAAUSDT"],
        listed_symbols={"BTCUSDT", "AAAUSDT"},
    )

    assert valid == ["AAAUSDT", "BTCUSDT"]
    assert invalid == ["BBBUSDT"]


def test_split_universe_handles_all_valid_symbols() -> None:
    valid, invalid = split_universe(
        configured_symbols=["BTCUSDT", "ETHUSDT"],
        listed_symbols={"BTCUSDT", "ETHUSDT", "SOLUSDT"},
    )

    assert valid == ["BTCUSDT", "ETHUSDT"]
    assert invalid == []
