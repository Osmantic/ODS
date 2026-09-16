"""The read-only diagnostics endpoint survives persisted JSON decoder limits."""

import json
from pathlib import Path

import pytest


SCHEMA = Path(__file__).resolve().parents[4] / "config/model-state.schema.v1.json"
VALID = {
    "schema": "ods.model-state.v1", "seq": 0, "routeSeq": 0,
    "desired": None, "active": None, "history": [],
    "availability": {"mode": "serve_active", "queueDeadline": None},
}


@pytest.mark.parametrize("nested", ["[" * 2000 + "0" + "]" * 2000,
                                    '{"x":' * 2000 + "0" + "}" * 2000], ids=["array", "object"])
def test_deep_state_remains_diagnostic_and_recovers(test_client, monkeypatch, tmp_path, nested):
    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ODS_MODEL_STATE_SCHEMA_PATH", str(SCHEMA))
    path = tmp_path / "model-state.json"
    path.write_text(nested, encoding="utf-8")

    denied = test_client.get("/api/models/state")
    assert denied.status_code in (401, 403)
    response = test_client.get("/api/models/state", headers=test_client.auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True and data["valid"] is False
    assert data["active"] is None and data["history"] == []
    assert data["errors"] and "not valid JSON" in data["errors"][0]
    assert path.read_text(encoding="utf-8") == nested

    path.write_text(json.dumps(VALID), encoding="utf-8")
    response = test_client.get("/api/models/state", headers=test_client.auth_headers)
    assert response.status_code == 200 and response.json()["valid"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == VALID


def test_deep_schema_is_unavailable_without_promoting_valid_state(test_client, monkeypatch, tmp_path):
    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    schema = tmp_path / "schema.json"
    schema.write_text('{"x":' * 2000 + "0" + "}" * 2000, encoding="utf-8")
    monkeypatch.setenv("ODS_MODEL_STATE_SCHEMA_PATH", str(schema))
    path = tmp_path / "model-state.json"
    path.write_text(json.dumps(VALID), encoding="utf-8")
    original = path.read_bytes()
    response = test_client.get("/api/models/state", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "schema unavailable or invalid" in response.json()["errors"][0]
    assert path.read_bytes() == original
