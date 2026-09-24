"""Regression tests for rejecting non-runnable n8n catalog templates."""

import json
from unittest.mock import patch


def _catalog(tmp_path, monkeypatch, workflow):
    import routers.workflows as workflows

    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps({
        "workflows": [{
            "id": "starter",
            "name": "Starter",
            "description": "test",
            "file": "starter.json",
            "dependencies": [],
        }],
        "categories": {},
    }))
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "starter.json").write_text(json.dumps(workflow))
    monkeypatch.setattr(workflows, "WORKFLOW_CATALOG_FILE", catalog_file)
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", workflow_dir)


def test_enable_rejects_disconnected_manual_starter_before_contacting_n8n(
    test_client, tmp_path, monkeypatch,
):
    _catalog(tmp_path, monkeypatch, {
        "name": "Starter",
        "nodes": [
            {
                "name": "Start",
                "type": "n8n-nodes-base.manualTrigger",
            },
            {
                "name": "Setup Instructions",
                "type": "n8n-nodes-base.stickyNote",
            },
        ],
        "connections": {},
    })

    with patch("routers.workflows.aiohttp.ClientSession") as session:
        response = test_client.post(
            "/api/workflows/starter/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "Workflow template is a non-runnable starter stub; "
            "no workflow was imported"
        )
    }
    session.assert_not_called()


def test_runnable_check_requires_a_trigger_connected_to_work(
    test_client, tmp_path, monkeypatch,
):
    _catalog(tmp_path, monkeypatch, {
        "name": "Starter",
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
            {"name": "One", "type": "n8n-nodes-base.set"},
            {"name": "Two", "type": "n8n-nodes-base.set"},
        ],
        "connections": {
            "One": {"main": [[{"node": "Two", "type": "main", "index": 0}]]}
        },
    })

    with patch("routers.workflows.aiohttp.ClientSession") as session:
        response = test_client.post(
            "/api/workflows/starter/enable",
            headers=test_client.auth_headers,
        )

    assert response.status_code == 422
    session.assert_not_called()
