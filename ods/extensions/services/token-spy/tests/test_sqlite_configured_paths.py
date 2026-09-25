"""Start a real API process with DB_PATH and read back persisted telemetry."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SERVICE = Path(__file__).resolve().parents[1]
INGEST = """
import asyncio
import httpx
import main

async def run():
    main.on_startup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
            base_url="http://token-spy.test",
            headers={"Authorization": "Bearer database-path-fixture"}) as client:
        response = await client.post("/api/ingest/routed", json={
            "agent": "fixture-agent", "model": "fixture-model",
            "provider_name": "local", "path": "/v1/chat/completions",
            "input_tokens": 13, "output_tokens": 7})
        assert response.status_code == 202, response.text
asyncio.run(run())
"""


@pytest.mark.parametrize("kind", ["filename", "relative-parent", "absolute"])
def test_configured_path_accepts_ingest_across_process_restarts(tmp_path, kind):
    working = tmp_path / "workspace with spaces"
    working.mkdir()
    configured = {
        "filename": "usage.db",
        "relative-parent": "metrics with spaces/usage.db",
        "absolute": str(tmp_path / "absolute metrics" / "usage.db"),
    }[kind]
    target = Path(configured) if Path(configured).is_absolute() else working / configured
    env = dict(os.environ, PYTHONPATH=str(SERVICE), DB_BACKEND="sqlite",
               DB_PATH=configured, TOKEN_SPY_API_KEY="database-path-fixture",
               LOCAL_MODEL_AGENTS="", AGENT_SESSION_DIRS="{}")
    for expected in (1, 2):
        result = subprocess.run([sys.executable, "-c", INGEST], cwd=working, env=env,
                                text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert target.is_file()
        with sqlite3.connect(target) as conn:
            assert conn.execute("SELECT count(*), sum(input_tokens), sum(output_tokens) FROM usage").fetchone() == (
                expected, 13 * expected, 7 * expected)
            assert conn.execute("SELECT DISTINCT agent, provider_name, cost_source FROM usage").fetchall() == [
                ("fixture-agent", "local", "local_zero_cost")]
