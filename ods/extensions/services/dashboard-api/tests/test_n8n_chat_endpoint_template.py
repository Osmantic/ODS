"""Import-boundary contract for the shipped Chat API Endpoint workflow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_FILE = ROOT / "config" / "n8n" / "01-chat-endpoint.json"


def _response_context(response):
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_chat_endpoint_public_enable_imports_and_activates_runnable_graph(
    test_client, monkeypatch,
):
    import routers.workflows as workflows

    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(workflows, "WORKFLOW_CATALOG_FILE", WORKFLOW_FILE.parent / "catalog.json")
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", WORKFLOW_FILE.parent)
    monkeypatch.setattr(
        workflows,
        "check_workflow_dependencies",
        AsyncMock(return_value={"llama-server": True}),
    )

    create_response = AsyncMock()
    create_response.status = 201
    create_response.json = AsyncMock(return_value={"data": {"id": "chat-endpoint-1"}})
    activate_response = AsyncMock()
    activate_response.status = 200

    session = AsyncMock()
    session.post = MagicMock(return_value=_response_context(create_response))
    session.patch = MagicMock(return_value=_response_context(activate_response))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session):
        response = test_client.post(
            "/api/workflows/chat-endpoint/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["activated"] is True
    assert response.json()["n8nId"] == "chat-endpoint-1"
    assert session.post.call_args.kwargs["json"] == workflow
    session.patch.assert_called_once()


def test_chat_endpoint_graph_exposes_bounded_non_streaming_llama_request():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    by_name = {node["name"]: node for node in workflow["nodes"]}

    assert "Start" not in by_name
    assert by_name["Receive Chat Request"]["parameters"] == {
        "httpMethod": "POST",
        "path": "ods-chat-completions",
        "responseMode": "responseNode",
        "options": {},
    }

    completion = by_name["Complete With llama-server"]
    assert completion["parameters"]["url"] == (
        "http://llama-server:8080/v1/chat/completions"
    )
    assert completion["parameters"]["options"]["timeout"] == 180000
    assert completion["onError"] == "continueRegularOutput"
    request_body = completion["parameters"]["jsonBody"]
    assert "messages: $json.messages" in request_body
    assert "max_tokens: $json.max_tokens" in request_body
    assert "stream: false" in request_body

    connections = workflow["connections"]
    assert connections["Request Valid?"]["main"][1][0]["node"] == (
        "Return Chat Error"
    )
    assert connections["Completion Ready?"]["main"][1][0]["node"] == (
        "Return Chat Error"
    )


def test_chat_endpoint_validates_input_and_sanitizes_upstream_output():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    validation_code = by_name["Validate Chat Request"]["parameters"]["jsCode"]
    normalize_code = by_name["Normalize Chat Completion"]["parameters"]["jsCode"]

    for contract in (
        "messages.length > 50",
        "allowedRoles.has(message.role)",
        "message.content.length > 16000",
        "totalCharacters > 32000",
        "body.stream === true",
        "maxTokens > 4096",
        "temperature > 2",
    ):
        assert contract in validation_code

    assert "response.choices" in normalize_code
    assert "message: { role: 'assistant', content }" in normalize_code
    assert "prompt_tokens: tokenCount" in normalize_code
    assert "statusCode: 502" in normalize_code
    assert "...response" not in normalize_code
