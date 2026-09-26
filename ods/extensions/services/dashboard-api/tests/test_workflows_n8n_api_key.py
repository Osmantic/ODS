"""The n8n API key must be configurable where the warning says to set it.

workflows.py tells operators to "set N8N_API_KEY in .env", but the key was
read once from the dashboard-api process environment, which Compose never
populates, and the schema did not list it, so the Settings editor refused it.
Every workflow call therefore reached n8n unauthenticated and got 401.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
import routers.workflows as wf_mod

SCHEMA = Path(__file__).resolve().parents[4] / ".env.schema.json"


def _session_capturing(seen):
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"data": [{"id": "1", "name": "Demo"}]})
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)

    def get(url, headers=None, **kwargs):
        seen["headers"] = headers
        return ctx

    session = AsyncMock()
    session.get = MagicMock(side_effect=get)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def test_workflow_listing_sends_key_from_install_env(monkeypatch, tmp_path):
    monkeypatch.delenv("N8N_API_KEY", raising=False)
    (tmp_path / ".env").write_text("N8N_API_KEY=n8n-key-from-env-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    seen = {}

    with patch("routers.workflows.aiohttp.ClientSession", return_value=_session_capturing(seen)):
        result = asyncio.run(wf_mod.get_n8n_workflows())

    assert result == [{"id": "1", "name": "Demo"}]
    assert seen["headers"] == {"X-N8N-API-KEY": "n8n-key-from-env-file"}


def test_workflow_listing_omits_key_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("N8N_API_KEY", raising=False)
    (tmp_path / ".env").write_text("N8N_USER=admin@ods.local\n", encoding="utf-8")
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    seen = {}

    with patch("routers.workflows.aiohttp.ClientSession", return_value=_session_capturing(seen)):
        asyncio.run(wf_mod.get_n8n_workflows())

    assert seen["headers"] == {}


def test_schema_accepts_n8n_api_key_as_secret():
    properties = json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]

    assert properties["N8N_API_KEY"]["type"] == "string"
    assert properties["N8N_API_KEY"]["secret"] is True
