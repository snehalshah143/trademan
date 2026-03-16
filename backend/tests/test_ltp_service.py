"""
Unit tests for LTPService tick processing and staleness watchdog.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from services.ltp.ltp_service import LTPService


@pytest.mark.asyncio
async def test_ltp_updates_redis_cache(mock_redis):
    """_on_tick must call set_ltp, set_tick_detail, and publish_tick with correct args."""
    svc = LTPService()
    ts = datetime.now(timezone.utc).isoformat()
    tick = {"symbol": "NIFTY", "ltp": 22500.0, "change": 12.5, "ts": ts}

    await svc._on_tick(tick)

    mock_redis.set_ltp.assert_called_once_with("NIFTY", 22500.0)
    mock_redis.set_tick_detail.assert_called_once_with("NIFTY", 22500.0, 12.5, ts)
    mock_redis.publish_tick.assert_called_once_with(tick)


@pytest.mark.asyncio
async def test_stale_warning_broadcast(mock_redis, monkeypatch):
    """Staleness watchdog must broadcast a STALE_WARNING when tick age exceeds threshold."""
    broadcast_calls: list = []

    class _Hub:
        async def broadcast(self, msg: str) -> None:
            broadcast_calls.append(json.loads(msg))

    import services.ltp.ltp_service as ltp_mod
    monkeypatch.setattr(ltp_mod, "hub", _Hub())
    object.__setattr__(ltp_mod.settings, "ltp_stale_threshold_seconds", 1.0)

    svc = LTPService()
    # Simulate a last tick that is 60 seconds old (well past the 1.0 s threshold)
    svc._last_tick_ts = datetime.now(timezone.utc) - timedelta(seconds=60)
    svc._running = True

    # Replace asyncio.sleep so the watchdog fires without real blocking;
    # disabling _running after the first sleep causes the loop to exit cleanly.
    async def _instant_sleep(t: float) -> None:
        svc._running = False

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    await svc._staleness_watchdog()

    assert len(broadcast_calls) == 1
    payload = broadcast_calls[0]
    assert payload["type"] == "STALE_WARNING"
    assert payload["age_seconds"] > 1.0
