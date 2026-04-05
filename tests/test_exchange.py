from __future__ import annotations

from exchange import is_rate_limited_payload


def test_is_rate_limited_payload_detects_bybit_retcode() -> None:
    assert is_rate_limited_payload({"retCode": 10006}) is True
    assert is_rate_limited_payload({"retCode": 0}) is False
    assert is_rate_limited_payload({}) is False
