"""Import-boundary contract for the shipped Voice Memo workflow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_FILE = ROOT / "config" / "n8n" / "voice-memo.json"


def _response_context(response):
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_voice_memo_public_enable_imports_and_activates_runnable_graph(
    test_client, monkeypatch,
):
    import routers.workflows as workflows

    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(workflows, "WORKFLOW_CATALOG_FILE", WORKFLOW_FILE.parent / "catalog.json")
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", WORKFLOW_FILE.parent)
    monkeypatch.setattr(
        workflows,
        "check_workflow_dependencies",
        AsyncMock(return_value={"whisper": True}),
    )

    create_response = AsyncMock()
    create_response.status = 201
    create_response.json = AsyncMock(return_value={"data": {"id": "voice-memo-1"}})
    activate_response = AsyncMock()
    activate_response.status = 200

    session = AsyncMock()
    session.post = MagicMock(return_value=_response_context(create_response))
    session.patch = MagicMock(return_value=_response_context(activate_response))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session):
        response = test_client.post(
            "/api/workflows/voice-memo/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["activated"] is True
    assert response.json()["n8nId"] == "voice-memo-1"
    assert session.post.call_args.kwargs["json"] == workflow
    session.patch.assert_called_once()


def test_voice_memo_graph_preserves_binary_until_bounded_whisper_request():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    by_name = {node["name"]: node for node in workflow["nodes"]}

    assert "Start" not in by_name
    assert by_name["Upload Voice Memo"]["type"] == "n8n-nodes-base.webhook"
    assert by_name["Upload Voice Memo"]["parameters"] == {
        "httpMethod": "POST",
        "path": "ods-voice-memo",
        "responseMode": "responseNode",
        "options": {},
    }
    assert by_name["List Whisper Models"]["parameters"]["url"] == (
        "http://whisper:8000/v1/models"
    )
    assert by_name["List Whisper Models"]["onError"] == "continueRegularOutput"

    transcribe = by_name["Transcribe With Whisper"]["parameters"]
    assert transcribe["url"] == "http://whisper:8000/v1/audio/transcriptions"
    assert transcribe["contentType"] == "multipart-form-data"
    assert transcribe["options"]["timeout"] == 180000
    assert by_name["Transcribe With Whisper"]["onError"] == (
        "continueRegularOutput"
    )
    body_parameters = transcribe["bodyParameters"]["parameters"]
    assert {
        "parameterType": "formBinaryData",
        "name": "file",
        "inputDataFieldName": "data",
    } in body_parameters
    assert {"name": "model", "value": "={{ $json.model }}"} in body_parameters

    connections = workflow["connections"]
    upload_branches = connections["Upload Valid?"]["main"][0]
    assert {"node": "Merge Upload and Models", "type": "main", "index": 0} in upload_branches
    assert connections["List Whisper Models"]["main"][0][0] == {
        "node": "Merge Upload and Models",
        "type": "main",
        "index": 1,
    }
    assert connections["Model Available?"]["main"][1][0]["node"] == (
        "Return Voice Memo Error"
    )
    assert connections["Transcript Ready?"]["main"][1][0]["node"] == (
        "Return Voice Memo Error"
    )


def test_voice_memo_validation_rejects_non_audio_and_unavailable_models():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    validation_code = by_name["Validate Audio Upload"]["parameters"]["jsCode"]
    model_code = by_name["Select Available Model"]["parameters"]["jsCode"]
    normalize_code = by_name["Normalize Transcript"]["parameters"]["jsCode"]

    assert "input.binary?.data" in validation_code
    assert "statusCode: 415" in validation_code
    assert "requestedModel.length > 200" in validation_code
    assert "!models.includes(requested)" in model_code
    assert "$('Validate Audio Upload').first()" in model_code
    assert "binary: upload.binary" in model_code
    assert "statusCode: 503" in model_code
    assert "statusCode: 502" in normalize_code
    assert "response.text" in normalize_code
