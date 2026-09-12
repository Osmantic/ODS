from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import extension_planning as api


AUTH = {"Authorization": "Bearer test-key-12345"}
HOST_STATE = {
    "odsVersion": "2.1.0",
    "platform": "linux",
    "architecture": "amd64",
    "containerRuntime": "docker",
    "gpuBackend": "cpu",
    "driverVersion": None,
    "available": {
        "diskBytes": 1_000_000,
        "ramBytes": 1_000_000,
        "vramBytes": 0,
        "cpuMillicores": 4_000,
        "gpuCount": 0,
    },
    "occupiedPorts": [],
    "reservedResources": [],
    "installedServices": [],
}
POLICY = {
    "allowedTrustTiers": ["bundled"],
    "forbiddenHostPermissions": ["docker-socket", "privileged"],
    "allowExperimental": False,
    "requireApproval": True,
}


def catalog_entry(
    service_id: str,
    *,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    priority: int = 0,
    secrets: list[str] | None = None,
) -> dict:
    return {
        "id": service_id,
        "manifest_schema_version": "ods.services.v2",
        "planning": {
            "serviceType": "docker",
            "version": "1.2.3",
            "dataSchemaVersion": "1",
            "odsCompatibility": {"minimum": "2.0.0", "maximum": None},
            "definitionSha256": "sha256:" + "d" * 64,
            "composeSha256": "",
            "dependsOn": [],
            "provides": provides or [],
            "requires": requires or [],
            "optional": [],
            "conflicts": [],
            "providerPriority": priority,
            "requirements": {
                "platforms": ["linux"],
                "architectures": ["amd64"],
                "containerRuntimes": ["docker"],
                "gpuBackends": ["cpu"],
                "minDriverVersion": None,
            },
            "estimates": {
                "downloadBytes": 100,
                "diskBytes": 200,
                "cpuMillicores": 100,
                "ramBytes": 300,
                "vramBytes": 0,
                "gpuCount": 0,
            },
            "resources": {
                "hostPorts": [],
                "containerPorts": [],
                "networks": [],
                "volumes": [],
                "devices": [],
                "exclusive": [],
                "linuxCapabilities": [],
                "hostPermissions": ["network"],
            },
            "configuration": [
                {
                    "key": key,
                    "type": "string",
                    "required": True,
                    "secret": True,
                    "source": "user",
                    "restartBehavior": "service",
                }
                for key in (secrets or [])
            ],
            "artifacts": {
                "images": [
                    {
                        "reference": f"example/{service_id}:1.2.3",
                        "digest": "sha256:" + "e" * 64,
                        "downloadBytes": 100,
                    }
                ],
                "builds": [],
            },
            "lifecycle": {
                "healthChecks": ["http:/health"],
                "readiness": ["healthy"],
                "setupHook": None,
                "migrationHook": None,
                "rollback": "definition",
                "timeoutSeconds": 120,
            },
            "data": [],
            "trust": {
                "tier": "bundled",
                "publisher": "ODS",
                "definitionSignature": None,
            },
            "support": {"status": "supported", "url": None},
            "legacy": False,
        },
    }


@pytest.fixture
def client(monkeypatch):
    entries = [
        catalog_entry("app", requires=["route@1"], secrets=["APP_TOKEN"]),
        catalog_entry("provider", provides=["route@1"], priority=1),
    ]
    monkeypatch.setattr(api, "EXTENSION_CATALOG", entries)
    monkeypatch.setattr(api, "EXTENSION_CATALOG_REVISION", api._computed_catalog_revision(entries))
    monkeypatch.setattr(api, "EXTENSION_PLANNING_POLICY", POLICY)
    import security

    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-key-12345")
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as test_client:
        yield test_client


def request_body(**changes) -> dict:
    body = {
        "catalogRevision": api.EXTENSION_CATALOG_REVISION,
        "requestedAction": "ensure",
        "observedStateRevision": hashlib.sha256(api.canonical_json_bytes(HOST_STATE)).hexdigest(),
        "observedState": HOST_STATE,
        "policyRevision": hashlib.sha256(api.canonical_json_bytes(POLICY)).hexdigest(),
        "validUntil": "2026-10-01T00:00:00Z",
        "requestedServices": ["app"],
        "requestedCapabilities": [],
        "providerPreferences": {},
        "missingConfigKeys": [],
        "missingSecretKeys": ["APP_TOKEN", "UNRELATED_TOKEN"],
    }
    body.update(changes)
    return body


def test_plan_requires_authentication(client) -> None:
    response = client.post("/api/extensions/plan", json=request_body())
    assert response.status_code == 401


def test_planning_contract_exposes_only_revisions_and_requires_auth(client) -> None:
    assert client.get("/api/extensions/planning-contract").status_code == 401
    response = client.get("/api/extensions/planning-contract", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema": "ods.assistant-first.planning-contract.v1",
        "catalogRevision": api.EXTENSION_CATALOG_REVISION,
        "policyRevision": hashlib.sha256(api.canonical_json_bytes(POLICY)).hexdigest(),
        "planSchema": "ods.assistant-first.plan.v1",
    }


def test_plan_is_deterministic_revision_bound_and_secret_name_only(client) -> None:
    first = client.post("/api/extensions/plan", headers=AUTH, json=request_body())
    second = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(requestedServices=list(reversed(["app"]))),
    )
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["cache-control"] == "no-store"
    plan = first.json()["plan"]
    assert plan["selectedServices"] == ["provider", "app"]
    assert plan["missingRequiredSecretKeys"] == ["APP_TOKEN"]
    assert "UNRELATED_TOKEN" not in first.text
    assert "=" not in first.text


def test_stale_catalog_revision_fails_with_current_revision(client) -> None:
    response = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(catalogRevision="b" * 64),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale-catalog-revision"
    assert response.json()["error"]["currentCatalogRevision"] == api.EXTENSION_CATALOG_REVISION
    assert response.headers["cache-control"] == "no-store"


def test_mismatched_host_state_revision_is_rejected_without_replanning(client) -> None:
    changed = json.loads(json.dumps(HOST_STATE))
    changed["occupiedPorts"] = [{"port": 8080, "protocol": "tcp", "owner": "other"}]
    response = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(observedState=changed),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale-observed-state"
    assert response.headers["cache-control"] == "no-store"


def test_client_cannot_supply_or_broaden_server_policy(client) -> None:
    response = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(policy={**POLICY, "allowedTrustTiers": ["community"]}),
    )
    assert response.status_code == 422
    stale = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(policyRevision="f" * 64),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale-policy"


@pytest.mark.parametrize(
    "invalid_policy",
    [
        {},
        {**POLICY, "unknownPolicyControl": True},
        {**POLICY, "allowedTrustTiers": []},
    ],
)
def test_missing_or_malformed_server_policy_fails_closed(
    client, monkeypatch, invalid_policy
) -> None:
    monkeypatch.setattr(api, "EXTENSION_PLANNING_POLICY", invalid_policy)
    contract = client.get("/api/extensions/planning-contract", headers=AUTH)
    assert contract.status_code == 503
    assert contract.json()["detail"] == "Planning contract is invalid"
    assert contract.headers["cache-control"] == "no-store"

    response = client.post("/api/extensions/plan", headers=AUTH, json=request_body())
    assert response.status_code == 503
    assert response.json()["detail"] == "Planning policy is invalid"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "change",
    [
        {"surprise": True},
        {"requestedServices": ["../escape"]},
        {"providerPreferences": {"route@1": "../../escape"}},
        {"missingSecretKeys": ["TOKEN=private-sentinel"]},
        {"observedStateRevision": "../state"},
    ],
)
def test_unknown_path_like_and_secret_value_inputs_fail_closed(client, change) -> None:
    response = client.post("/api/extensions/plan", headers=AUTH, json=request_body(**change))
    assert response.status_code == 422
    assert "private-sentinel" not in response.text


def test_duplicate_json_keys_and_floats_are_rejected(client) -> None:
    duplicate = json.dumps(request_body())[:-1] + ',"requestedServices":[]}'
    response = client.post(
        "/api/extensions/plan",
        headers={**AUTH, "Content-Type": "application/json"},
        content=duplicate,
    )
    assert response.status_code == 422
    floated = json.dumps(request_body())[:-1] + ',"unused":1.5}'
    response = client.post(
        "/api/extensions/plan",
        headers={**AUTH, "Content-Type": "application/json"},
        content=floated,
    )
    assert response.status_code == 422


def test_ambiguous_provider_returns_safe_domain_error(client, monkeypatch) -> None:
    entries = [
        catalog_entry("alpha", provides=["route@1"]),
        catalog_entry("beta", provides=["route@1"]),
    ]
    monkeypatch.setattr(api, "EXTENSION_CATALOG", entries)
    revision = api._computed_catalog_revision(entries)
    monkeypatch.setattr(api, "EXTENSION_CATALOG_REVISION", revision)
    response = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(
            catalogRevision=revision,
            requestedServices=[],
            requestedCapabilities=["route@1"],
        ),
    )
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "ambiguous-provider",
        "details": {"capability": "route@1", "candidates": ["alpha", "beta"]},
    }


def test_tampered_catalog_revision_fails_before_planning(client, monkeypatch) -> None:
    monkeypatch.setattr(api, "EXTENSION_CATALOG_REVISION", "f" * 64)
    response = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(catalogRevision="f" * 64),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Planning catalog revision is invalid"


def test_revision_matching_but_structurally_invalid_catalog_is_server_error(client, monkeypatch) -> None:
    entries = [catalog_entry("app")]
    entries[0]["planning"]["requirements"] = []
    revision = api._computed_catalog_revision(entries)
    monkeypatch.setattr(api, "EXTENSION_CATALOG", entries)
    monkeypatch.setattr(api, "EXTENSION_CATALOG_REVISION", revision)
    response = client.post(
        "/api/extensions/plan",
        headers=AUTH,
        json=request_body(catalogRevision=revision, requestedServices=["app"]),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Planning catalog is invalid"
    assert response.headers["cache-control"] == "no-store"


def test_oversized_request_is_rejected(client) -> None:
    response = client.post(
        "/api/extensions/plan",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b"{" + b" " * api.MAX_REQUEST_BYTES + b"}",
    )
    assert response.status_code == 413


def test_endpoint_has_no_host_observer_or_lifecycle_dependencies() -> None:
    forbidden = {"subprocess", "httpx", "aiohttp", "socket", "pathlib", "docker"}
    assert forbidden.isdisjoint(set(api.plan_extensions.__globals__))
