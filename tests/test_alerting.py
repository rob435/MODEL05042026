from __future__ import annotations

import asyncio

from alerting import AlertPayload, TelegramNotifier


def test_entry_ready_alert_text_uses_live_tradeable_label() -> None:
    asyncio.run(_exercise_entry_ready_alert_text())


async def _exercise_entry_ready_alert_text() -> None:
    captured: list[str] = []

    class RecordingNotifier(TelegramNotifier):
        async def _send_text(self, text: str) -> bool:
            captured.append(text)
            return True

    notifier = RecordingNotifier(session=None, bot_token="token", chat_id="chat")  # type: ignore[arg-type]
    await notifier.send(
        AlertPayload(
            stage="emerging",
            signal_kind="entry_ready",
            ticker="TRXUSDT",
            composite_score=1.31,
            momentum_z=1.10,
            curvature=0.000026,
            hurst=0.726,
            current_price=0.318750,
            regime_score=1,
            rank=3,
            persistence_hits=1,
            persistence_window=3,
            persistence_min_hits=2,
        )
    )

    assert captured
    assert "tradeable intrabar entry candidate" in captured[0]
    assert "Persistence: 1/3 (strong at 2/3)" in captured[0]
