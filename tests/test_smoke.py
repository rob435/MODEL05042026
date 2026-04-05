from __future__ import annotations

from smoke import should_fail_smoke


def test_should_fail_smoke_only_when_strict_and_invalid_symbols_exist() -> None:
    assert should_fail_smoke([], strict_universe=False) is False
    assert should_fail_smoke([], strict_universe=True) is False
    assert should_fail_smoke(["BADUSDT"], strict_universe=False) is False
    assert should_fail_smoke(["BADUSDT"], strict_universe=True) is True
