"""Import-boundary contract for the shipped email-to-task workflow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_DIR = ROOT / "config" / "n8n"
WORKFLOW_FILE = WORKFLOW_DIR / "email-to-task.json"


def _response_context(response):
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_email_to_task_public_enable_imports_the_runnable_workflow(
    test_client, monkeypatch,
):
    import routers.workflows as workflows

    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(workflows, "WORKFLOW_CATALOG_FILE", WORKFLOW_DIR / "catalog.json")
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", WORKFLOW_DIR)

    create_response = AsyncMock()
    create_response.status = 201
    create_response.json = AsyncMock(
        return_value={"id": "email-task-1", "data": {"id": "email-task-1"}}
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
            "/api/workflows/email-to-task/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["activated"] is True
    assert session.post.call_args_list[0].kwargs["json"] == workflow

    by_name = {node["name"]: node for node in workflow["nodes"]}
    assert by_name["Receive Email"]["parameters"] == {
        "httpMethod": "POST",
        "path": "ods-email-to-task",
        "responseMode": "responseNode",
        "options": {},
    }
    assert by_name["Extract Tasks with llama-server"]["parameters"]["url"] == (
        "http://llama-server:8080/v1/chat/completions"
    )


def test_email_to_task_catalog_and_output_contract_are_truthful():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    catalog = json.loads((WORKFLOW_DIR / "catalog.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["workflows"] if item["id"] == "email-to-task")
    by_name = {node["name"]: node for node in workflow["nodes"]}

    assert entry["name"] == workflow["name"]
    assert entry["dependencies"] == ["llama-server"]
    assert [node["id"] for node in entry["diagram"]["nodes"]] == [
        "receive",
        "validate",
        "llm",
        "respond",
    ]
    assert not any(
        node["type"] in {"n8n-nodes-base.manualTrigger", "n8n-nodes-base.emailReadImap"}
        for node in workflow["nodes"]
    )

    validation_code = by_name["Validate Email"]["parameters"]["jsCode"]
    assert "20000" in validation_code
    parser_code = by_name["Validate Extracted Tasks"]["parameters"]["jsCode"]
    assert "payload.tasks.length > 20" in parser_code
    assert "priorities.includes(priority)" in parser_code
    assert by_name["Return Model Error"]["parameters"]["options"]["responseCode"] == (
        "={{ $json.statusCode }}"
    )
