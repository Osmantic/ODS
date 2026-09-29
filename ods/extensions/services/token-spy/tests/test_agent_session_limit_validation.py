import asyncio
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_api_rejects_low_agent_limit_and_poller_preserves_short_session(
    monkeypatch, tmp_path,
):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "agent-limit-test-key")
    monkeypatch.setenv("AGENT_NAME", "proxy-agent")
    spec = importlib.util.spec_from_file_location(
        "token_spy_agent_limit_validation", service / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    session_dir = tmp_path / "victim-sessions"
    session_dir.mkdir()
    session = session_dir / "active.jsonl"
    session.write_text(json.dumps({
        "type": "message",
        "message": {"role": "user", "content": "short"},
    }) + "\n")
    monkeypatch.setattr(module, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(module, "AGENT_SESSION_DIRS", {"victim": str(session_dir)})
    monkeypatch.setattr(module, "LOCAL_MODEL_AGENTS", {"victim"})
    monkeypatch.setattr(module, "REMOTE_AGENTS", {})
    monkeypatch.setattr(module, "_db_available", False)

    with TestClient(module.app) as client:
        response = client.post(
            "/api/settings",
            headers={"Authorization": "Bearer agent-limit-test-key"},
            json={"agents": {"victim": {"session_char_limit": 1}}},
        )
    assert response.status_code == 400
    assert module.get_agent_setting("victim", "session_char_limit") == 200_000

    status = module._get_local_session_status("victim", include_session_id=True)
    assert status["recommendation"] == "healthy"
    assert status["current_history_chars"] == 5

    real_sleep = asyncio.sleep

    async def poll_once(delay):
        if delay == 10:
            return
        if delay == 60:
            raise asyncio.CancelledError
        await real_sleep(delay)

    monkeypatch.setattr(module.asyncio, "sleep", poll_once)

    async def run_poller_once():
        try:
            await module._poll_remote_agents()
        except asyncio.CancelledError:
            pass

    asyncio.run(run_poller_once())
    assert session.read_text().endswith("\n")
