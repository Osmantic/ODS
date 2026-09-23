"""The terms API exposes provenance only and performs no mutation."""
import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import models


def test_terms_endpoint_reports_review_state_without_agent_work(monkeypatch):
    catalog = Path(__file__).resolve().parents[4] / "config/model-library.json"
    record = json.loads(catalog.read_text(encoding="utf-8"))["models"][0]
    untouched = copy.deepcopy(record)
    monkeypatch.setattr(models, "_find_model_in_library", lambda _: record)
    monkeypatch.setattr(models, "_call_agent_model", lambda *a, **kw: pytest.fail("Terms inspection must not contact the host agent"))
    response = models.model_terms(record["id"], api_key="fixture")
    assert response["recordValid"] is True
    assert response["releaseReady"] is False
    assert response["terms"]["sources"][0]["role"] == "artifact_publisher"
    assert response["terms"]["commercial_use"] == "not_assessed"
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
