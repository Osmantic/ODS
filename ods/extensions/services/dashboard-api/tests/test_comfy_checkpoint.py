"""The Dashboard may only proxy the fixed, confirmed Comfy checkpoint action."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from host_agent_client import AgentHTTPError, AgentUnavailable
from routers import comfy_checkpoint


def test_status_and_confirmed_download_use_host_agent(monkeypatch):
    calls = []

    def fake_request(method, path, payload=None, timeout=None):
        calls.append((method, path, payload, timeout))
        return {"state": "downloading"}

    monkeypatch.setattr(comfy_checkpoint, "request_json", fake_request)
    assert comfy_checkpoint.checkpoint_status(api_key="test") == {"state": "downloading"}
    body = comfy_checkpoint.DownloadConfirmation(
        model_id="sdxl_lightning_4step", acknowledge_size_bytes=6_938_040_682,
    )
    assert comfy_checkpoint.checkpoint_download(body, api_key="test") == {"state": "downloading"}
    assert calls == [
        ("GET", "/v1/comfy/checkpoint/status", None, 10),
        ("POST", "/v1/comfy/checkpoint/download", body.model_dump(), 10),
    ]


def test_url_and_path_inputs_are_rejected_by_dashboard_schema():
    with pytest.raises(ValidationError):
        comfy_checkpoint.DownloadConfirmation(
            model_id="sdxl_lightning_4step", acknowledge_size_bytes=6_938_040_682,
            url="https://example.invalid/model", target_path="/tmp/model",
        )


def test_host_refusal_and_outage_keep_action_unavailable(monkeypatch):
    def refused(*_args, **_kwargs):
        raise AgentHTTPError(409, "comfyui_not_selected")

    monkeypatch.setattr(comfy_checkpoint, "request_json", refused)
    with pytest.raises(HTTPException) as failure:
        comfy_checkpoint.checkpoint_cancel(api_key="test")
    assert failure.value.status_code == 409

    def unavailable(*_args, **_kwargs):
        raise AgentUnavailable("offline")

    monkeypatch.setattr(comfy_checkpoint, "request_json", unavailable)
    with pytest.raises(HTTPException) as failure:
        comfy_checkpoint.checkpoint_status(api_key="test")
    assert failure.value.status_code == 503
