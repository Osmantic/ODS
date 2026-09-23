"""The terms API exposes provenance only and performs no mutation."""
import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import models
from model_terms import project_terms


def catalog_record():
    catalog = Path(__file__).resolve().parents[4] / "config/model-library.json"
    return json.loads(catalog.read_text(encoding="utf-8"))["models"][0]


def test_terms_endpoint_reports_review_state_without_agent_work(monkeypatch):
    record = catalog_record()
    untouched = copy.deepcopy(record)
    monkeypatch.setattr(models, "_find_model_in_library", lambda _: record)
    monkeypatch.setattr(models, "_call_agent_model", lambda *a, **kw: pytest.fail("Terms inspection must not contact the host agent"))
    response = models.model_terms(record["id"], api_key="fixture")
    assert response["recordValid"] is True
    assert response["releaseReady"] == project_terms(record)["releaseReady"]
    assert response["terms"]["sources"][0]["role"] == "artifact_publisher"
    assert response["terms"]["commercial_use"] == record["terms"]["commercial_use"]
    assert record == untouched
    response["terms"]["sources"].clear()
    assert record == untouched


def test_unknown_model_and_missing_terms_do_not_look_verified(monkeypatch):
    monkeypatch.setattr(models, "_find_model_in_library", lambda _: None)
    with pytest.raises(HTTPException) as missing:
        models.model_terms("unknown", api_key="fixture")
    assert missing.value.status_code == 404
    monkeypatch.setattr(models, "_find_model_in_library", lambda _: {"id": "imported"})
    response = models.model_terms("imported", api_key="fixture")
    assert response["recordValid"] is False
    assert response["terms"] is None
    assert response["releaseReady"] is False


@pytest.mark.parametrize("acknowledgement,status", [
    (None, 428), ({"acknowledged": "true"}, 428),
    ({"acknowledged": True, "termsDigest": "f" * 64}, 409),
])
def test_direct_download_cannot_skip_current_terms(test_client, monkeypatch, acknowledgement, status):
    record = catalog_record()
    monkeypatch.setattr(models, "_find_model_in_library", lambda _: record)
    monkeypatch.setattr(models, "_bootstrap_upgrade_download_conflict", lambda: None)
    monkeypatch.setattr(models, "_call_agent_model", lambda *a, **kw: pytest.fail("Unreviewed download reached host"))
    response = test_client.post(f"/api/models/{record['id']}/download", headers=test_client.auth_headers,
                                json={"termsAcknowledgement": acknowledgement})
    assert response.status_code == status
    assert response.json()["detail"]["modelId"] == record["id"]


def test_current_review_is_forwarded_and_metadata_change_requires_another_review(test_client, monkeypatch):
    record = catalog_record()
    monkeypatch.setattr(models, "_find_model_in_library", lambda _: record)
    monkeypatch.setattr(models, "_bootstrap_upgrade_download_conflict", lambda: None)
    calls = []
    monkeypatch.setattr(models, "_call_agent_model", lambda path, body: calls.append((path, body)) or {"status": "started"})
    acknowledgement = {"termsDigest": project_terms(record)["termsDigest"], "acknowledged": True,
                       "upstreamAccepted": True}
    response = test_client.post(f"/api/models/{record['id']}/download", headers=test_client.auth_headers,
                                json={"termsAcknowledgement": acknowledgement})
    assert response.status_code == 200
    assert calls[0][1]["termsAcknowledgement"] == acknowledgement
    record["terms"]["note"] = "A corrected source notice"
    response = test_client.post(f"/api/models/{record['id']}/download", headers=test_client.auth_headers,
                                json={"termsAcknowledgement": acknowledgement})
    assert response.status_code == 409
    assert len(calls) == 1


def test_hub_preview_is_read_only_and_import_requires_matching_review(test_client, monkeypatch, tmp_path):
    payload = {"id": "Publisher/Model", "sha": "a" * 40, "pipeline_tag": "text-generation",
               "cardData": {"license": "mit"}, "siblings": [{"rfilename": "model-Q4_K_M.gguf",
               "lfs": {"size": 1024, "sha256": "b" * 64}}]}
    async def hub(*args, **kwargs):
        return payload, {}
    calls = []
    monkeypatch.setattr(models, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_hf_get_json", hub)
    monkeypatch.setattr(models, "_bootstrap_upgrade_download_conflict", lambda: None)
    monkeypatch.setattr(models, "_call_agent_model", lambda path, body: calls.append(body) or {"status": "started"})
    response = test_client.get("/api/models/huggingface/repositories/Publisher/Model", headers=test_client.auth_headers)
    assert response.status_code == 200, response.text
    artifact = response.json()["artifacts"][0]
    preview = artifact["termsPreview"]
    assert preview["recordValid"] and not preview["releaseReady"]
    assert not calls and not (tmp_path / "model-imports.json").exists()
    body = {"repoId": "Publisher/Model", "artifactId": artifact["id"]}
    rejected = test_client.post("/api/models/huggingface/import", headers=test_client.auth_headers, json=body)
    assert rejected.status_code == 428
    assert not calls and not (tmp_path / "model-imports.json").exists()
    body["termsAcknowledgement"] = {"termsDigest": preview["termsDigest"], "acknowledged": True}
    accepted = test_client.post("/api/models/huggingface/import", headers=test_client.auth_headers, json=body)
    assert accepted.status_code == 200, accepted.text
    assert calls[0]["termsAcknowledgement"] == body["termsAcknowledgement"]
    payload["cardData"]["license"] = "apache-2.0"
    changed = test_client.post("/api/models/huggingface/import", headers=test_client.auth_headers, json=body)
    assert changed.status_code == 409
    assert len(calls) == 1


def test_host_enforces_its_own_catalog_review_before_lifecycle_or_download(monkeypatch):
    from test_host_agent import _FakeHandler, _mod
    record = catalog_record()
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod, "_load_model_library_records", lambda: [record])
    lifecycle_calls = []
    monkeypatch.setattr(_mod, "_begin_model_lifecycle", lambda *args: lifecycle_calls.append(args) or
                        (False, {"operation": "model_activation", "target": "another-model"}))
    payload = {"gguf_file": record["gguf_file"], "gguf_url": record["gguf_url"],
               "terms": {"review_status": "reviewed"}}
    if record.get("gguf_parts"):
        payload["gguf_parts"] = record["gguf_parts"]
    for acknowledgement, status in [(None, 428), ({"termsDigest": "a" * 64, "acknowledged": True}, 409)]:
        payload["termsAcknowledgement"] = acknowledgement
        handler = _FakeHandler(json.dumps(payload).encode())
        _mod.AgentHandler._handle_model_download(handler)
        assert handler.response_code == status, handler.parse_response()
        assert not lifecycle_calls
    payload["termsAcknowledgement"] = {"termsDigest": project_terms(record)["termsDigest"],
                                       "acknowledged": True, "upstreamAccepted": True}
    handler = _FakeHandler(json.dumps(payload).encode())
    _mod.AgentHandler._handle_model_download(handler)
    assert len(lifecycle_calls) == 1
    assert handler.response_code == 409
    assert handler.parse_response()["code"] == "model_lifecycle_busy"
