"""Process lifecycle.

Claude Desktop sometimes spawns a duplicate stdio server and abandons it
without closing stdin, so the abandoned process never sees EOF and idles
indefinitely. These cover the opt-in idle watchdog that reaps that case.
"""

from __future__ import annotations

import time

import pytest

from ferc_elibrary_mcp.server import IdleShutdownMiddleware, _start_idle_watchdog


async def test_activity_tracker_updates_on_message():
    tracker = IdleShutdownMiddleware()
    tracker.last_activity = time.monotonic() - 100

    async def call_next(_context):
        return "ok"

    result = await tracker.on_message(object(), call_next)
    assert result == "ok"
    assert time.monotonic() - tracker.last_activity < 1


def test_watchdog_fires_when_idle(monkeypatch):
    fired: list[int] = []
    monkeypatch.setattr(
        "ferc_elibrary_mcp.server.signal.raise_signal", lambda sig: fired.append(sig)
    )
    tracker = IdleShutdownMiddleware()
    tracker.last_activity = time.monotonic() - 60
    _start_idle_watchdog(tracker, timeout=1.0)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not fired:
        time.sleep(0.05)
    assert fired, "watchdog did not fire on an idle instance"


def test_watchdog_holds_off_while_active(monkeypatch):
    fired: list[int] = []
    monkeypatch.setattr(
        "ferc_elibrary_mcp.server.signal.raise_signal", lambda sig: fired.append(sig)
    )
    tracker = IdleShutdownMiddleware()
    _start_idle_watchdog(tracker, timeout=2.0)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        tracker.last_activity = time.monotonic()
        time.sleep(0.1)
    assert not fired, "watchdog reaped an instance that was still in use"


@pytest.mark.parametrize(
    "value,expected", [("0", False), ("", False), ("3600", True), ("0.5", True)]
)
def test_idle_timeout_is_opt_in(monkeypatch, value, expected):
    monkeypatch.setenv("FERC_MCP_IDLE_TIMEOUT_SECONDS", value or "0")
    import importlib

    from ferc_elibrary_mcp import config

    importlib.reload(config)
    try:
        assert (config.IDLE_SHUTDOWN_SECONDS > 0) is expected
    finally:
        monkeypatch.delenv("FERC_MCP_IDLE_TIMEOUT_SECONDS", raising=False)
        importlib.reload(config)
