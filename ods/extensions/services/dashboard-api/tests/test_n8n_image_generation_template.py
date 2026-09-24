"""Import-boundary contract for the shipped ComfyUI webhook workflow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_FILE = ROOT / "config" / "n8n" / "image-gen-webhook.json"


def _response_context(response):
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_image_generation_public_enable_imports_the_real_comfyui_graph(
    test_client, monkeypatch,
):
    import routers.workflows as workflows

    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        workflows, "WORKFLOW_CATALOG_FILE", WORKFLOW_FILE.parent / "catalog.json"
    )
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", WORKFLOW_FILE.parent)

    create_response = AsyncMock()
    create_response.status = 201
    create_response.json = AsyncMock(
        return_value={"id": "image-1", "data": {"id": "image-1"}}
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
            "/api/workflows/image-gen-webhook/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["activated"] is True
    assert session.post.call_args_list[0].kwargs["json"] == workflow

    by_name = {node["name"]: node for node in workflow["nodes"]}
    assert by_name["Generate Image Request"]["type"] == "n8n-nodes-base.webhook"
    assert by_name["Queue ComfyUI Prompt"]["parameters"]["url"] == (
        "http://comfyui:8188/prompt"
    )
    queue_body = by_name["Queue ComfyUI Prompt"]["parameters"]["jsonBody"]
    assert "sdxl_lightning_4step.safetensors" in queue_body
    assert "steps: 4" in queue_body
    assert "cfg: 1.0" in queue_body


def test_image_generation_uses_an_async_queue_and_status_contract():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    by_name = {node["name"]: node for node in workflow["nodes"]}

    assert by_name["Return Queue Receipt"]["parameters"]["options"][
        "responseCode"
    ] == 202
    assert by_name["Image Status Request"]["parameters"] == {
        "httpMethod": "GET",
        "path": "ods-image-status",
        "responseMode": "responseNode",
        "options": {},
    }
    assert by_name["Check ComfyUI History"]["parameters"]["url"] == (
        "={{ 'http://comfyui:8188/history/' + $json.prompt_id }}"
    )
    processing_branch = workflow["connections"]["Generation Finished?"]["main"][1]
    assert processing_branch == [
        {"node": "Return Processing Status", "type": "main", "index": 0}
    ]
    assert by_name["Return Processing Status"]["parameters"]["options"][
        "responseCode"
    ] == 202
    status_code = by_name["Inspect Generation Status"]["parameters"]["jsCode"]
    assert "encodeURIComponent" in status_code
    assert "URLSearchParams" not in status_code
    assert not any(node["type"] == "n8n-nodes-base.wait" for node in workflow["nodes"])
