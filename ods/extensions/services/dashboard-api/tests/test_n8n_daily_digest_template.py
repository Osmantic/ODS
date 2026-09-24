"""Import-boundary contract for the shipped Daily Digest workflow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_FILE = ROOT / "config" / "n8n" / "daily-digest.json"


def _response_context(response):
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_daily_digest_public_enable_imports_a_runnable_dual_trigger_graph(
    test_client, monkeypatch,
):
    import routers.workflows as workflows

    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    catalog_file = WORKFLOW_FILE.parent / "catalog.json"
    monkeypatch.setattr(workflows, "WORKFLOW_CATALOG_FILE", catalog_file)
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", WORKFLOW_FILE.parent)

    create_response = AsyncMock()
    create_response.status = 201
    create_response.json = AsyncMock(
        return_value={"id": "daily-1", "data": {"id": "daily-1"}}
    )
    activate_response = AsyncMock()
    activate_response.status = 200

    session = AsyncMock()
    session.post = MagicMock(
        side_effect=[
            _response_context(create_response),
            _response_context(activate_response),
        ]
    )
    session.patch = MagicMock(return_value=_response_context(activate_response))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session):
        response = test_client.post(
            "/api/workflows/daily-digest/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["activated"] is True
    assert session.post.call_args_list[0].kwargs["json"] == workflow

    by_name = {node["name"]: node for node in workflow["nodes"]}
    assert by_name["Capture Event"]["type"] == "n8n-nodes-base.webhook"
    assert by_name["Every Morning"]["type"] == "n8n-nodes-base.scheduleTrigger"
    assert by_name["Summarize With llama-server"]["parameters"]["url"] == (
        "http://llama-server:8080/v1/chat/completions"
    )


def test_daily_digest_acknowledges_events_only_after_the_file_write():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    connections = workflow["connections"]

    assert connections["Digest To File"]["main"][0][0]["node"] == "Save Digest"
    assert connections["Save Digest"]["main"][0][0]["node"] == (
        "Acknowledge Saved Events"
    )

    by_name = {node["name"]: node for node in workflow["nodes"]}
    store_code = by_name["Validate and Store Event"]["parameters"]["jsCode"]
    acknowledge_code = by_name["Acknowledge Saved Events"]["parameters"]["jsCode"]
    assert "events.length >= 100" in store_code
    assert "statusCode: 429" in store_code
    assert "current.filter" in acknowledge_code
    assert "included.has(event.id)" in acknowledge_code
