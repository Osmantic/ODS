"""Extension ids are plain names: a trailing newline or a path is never one."""
from __future__ import annotations

import json

import config


def test_a_trailing_newline_is_not_an_extension_id(test_client):
    resp = test_client.get("/api/extensions/n8n%0A/progress", headers=test_client.auth_headers)
    assert resp.status_code == 404


def test_shipped_catalog_entries_with_invalid_ids_are_dropped(tmp_path, monkeypatch):
    catalog = tmp_path / "extensions-catalog.json"
    catalog.write_text(json.dumps({"extensions": [
        {"id": "n8n", "name": "n8n"},
        {"id": "../x", "name": "escape"},
        {"id": "n8n\n", "name": "newline"},
        {"id": "N8N", "name": "upper"},
        {"name": "no id"},
        "not an entry",
    ]}), encoding="utf-8")
    monkeypatch.setattr(config, "CATALOG_PATH", catalog)

    assert config.load_extension_catalog() == [{"id": "n8n", "name": "n8n"}]
