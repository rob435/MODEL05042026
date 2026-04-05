from __future__ import annotations

from signal_engine import RankedTicker, SignalEngine


def test_format_top_rankings_renders_leaders() -> None:
    engine = SignalEngine.__new__(SignalEngine)
    engine.settings = type("Settings", (), {"ranking_log_top_n": 2})()
    engine.last_ranked_tickers = {
        "confirmed": [
            RankedTicker("AAAUSDT", 1, 2.5, 1.2, 1.3, 0.61),
            RankedTicker("BBBUSDT", 2, 1.7, 0.9, 0.8, 0.58),
            RankedTicker("CCCUSDT", 3, 1.1, 0.5, 0.6, 0.57),
        ],
        "emerging": [],
    }

    rendered = SignalEngine.format_top_rankings(engine, stage="confirmed")

    assert "#1 AAAUSDT score=2.50" in rendered
    assert "#2 BBBUSDT score=1.70" in rendered
    assert "CCCUSDT" not in rendered
