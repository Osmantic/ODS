"""A slow configured local session must not stall the HTTP proxy event loop."""
import asyncio
import importlib.util
from pathlib import Path
import threading

import httpx
import pytest


@pytest.mark.parametrize("operation", ["status", "reset"])
def test_health_responds_while_local_session_io_is_pending(monkeypatch, operation):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "poll-responsiveness-key")
    spec = importlib.util.spec_from_file_location("token_spy_poll_io", service / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "AGENT_NAME", "proxy-agent")
    monkeypatch.setattr(module, "REMOTE_AGENTS", {})
    monkeypatch.setattr(module, "AGENT_SESSION_DIRS", {"local-agent": "/unused-fixture"})
    monkeypatch.setattr(module, "get_agent_setting", lambda *_: 100)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_io(*args, **kwargs):
        entered.set()
        release.wait()
        finished.set()
        return {"current_history_chars": 200, "recommendation": "reset_recommended", "_reset_session_id": "fixture"}

    monkeypatch.setattr(module, "_get_local_session_status", blocked_io if operation == "status"
                        else lambda *args, **kwargs: {"current_history_chars": 200, "_reset_session_id": "fixture"})
    monkeypatch.setattr(module, "_kill_session", blocked_io if operation == "reset"
                        else lambda *args, **kwargs: {"action": "none"})
    real_sleep = asyncio.sleep

    async def poll_delay(delay):
        if delay == 10:
            return
        if delay == 60:
            raise asyncio.CancelledError
        await real_sleep(delay)

    monkeypatch.setattr(module.asyncio, "sleep", poll_delay)

    async def exercise():
        # Release even the old blocking implementation; failure is based on
        # operation ordering, not a narrow response-time threshold.
        watchdog = threading.Timer(6, release.set)
        watchdog.start()
        poll = asyncio.create_task(module._poll_remote_agents())
        try:
            assert await asyncio.to_thread(entered.wait, 6)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                       base_url="http://proxy.test") as client:
                response = await client.get("/health")
            assert response.status_code == 200
            assert not finished.is_set(), "health only responded after the blocked session I/O ended"
        finally:
            release.set()
            watchdog.cancel()
            try:
                await poll
            except asyncio.CancelledError:
                pass

    asyncio.run(exercise())
