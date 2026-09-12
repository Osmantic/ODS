from __future__ import annotations

import pytest

import extension_transaction_production as production
from assistant_first_planner import PlanningError
from extension_planning_contract import computed_catalog_revision


def catalog_entry(service_id: str = "notes") -> dict:
    return {
        "id": service_id,
        "manifest_schema_version": "ods.services.v1",
        "planning": {
            "serviceType": "docker",
            "version": "0.0.0-legacy",
            "dataSchemaVersion": "legacy",
            "odsCompatibility": {"minimum": "", "maximum": ""},
            "definitionSha256": "sha256:" + "a" * 64,
            "composeSha256": "sha256:" + "b" * 64,
            "dependsOn": [],
            "legacy": True,
        },
    }


def test_production_catalog_is_cloned_and_revision_verified(monkeypatch):
    entries = [catalog_entry()]
    revision = computed_catalog_revision(entries)
    monkeypatch.setattr(production, "EXTENSION_CATALOG", entries)
    monkeypatch.setattr(production, "EXTENSION_CATALOG_REVISION", revision)

    first, first_revision = production.production_catalog()
    first[0]["id"] = "changed"
    second, second_revision = production.production_catalog()

    assert first_revision == second_revision == revision
    assert second == entries
    monkeypatch.setattr(production, "EXTENSION_CATALOG_REVISION", "f" * 64)
    with pytest.raises(PlanningError, match="extension-catalog-revision-mismatch"):
        production.production_catalog()


def test_observed_state_uses_injected_paths_and_cached_health(
    monkeypatch, tmp_path
):
    install = tmp_path / "install"
    data = tmp_path / "data"
    user = data / "user-extensions"
    builtin = install / "extensions" / "services"
    service = builtin / "notes"
    service.mkdir(parents=True)
    data.mkdir()
    (service / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (install / ".env").write_text("ODS_VERSION=2.6.0\n", encoding="utf-8")
    monkeypatch.setattr(production.platform, "system", lambda: "Linux")
    monkeypatch.setattr(production.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(production, "GPU_BACKEND", "cpu")
    monkeypatch.setattr(production, "_available_ram_bytes", lambda: 4096)
    monkeypatch.setattr(production, "_gpu_availability", lambda: (0, 0))
    monkeypatch.setenv("ODS_CONTAINER_RUNTIME", "docker")

    state = production.production_observed_state(
        install_dir=install,
        data_dir=data,
        user_extensions_dir=user,
        builtin_extensions_dir=builtin,
        catalog_entries=[catalog_entry()],
        cached_statuses={"notes": "healthy"},
    )

    assert state["odsVersion"] == "2.6.0"
    assert state["platform"] == "linux"
    assert state["architecture"] == "amd64"
    assert state["containerRuntime"] == "docker"
    assert state["gpuBackend"] == "cpu"
    assert state["available"]["ramBytes"] == 4096
    assert state["installedServices"] == [
        {
            "id": "notes",
            "version": "0.0.0-legacy",
            "definitionSha256": "sha256:" + "a" * 64,
            "status": "enabled",
        }
    ]

    progress = data / "extension-progress"
    progress.mkdir()
    (progress / "notes.json").write_text('{"status":"error"}', encoding="utf-8")
    failed = production.production_observed_state(
        install_dir=install,
        data_dir=data,
        user_extensions_dir=user,
        builtin_extensions_dir=builtin,
        catalog_entries=[catalog_entry()],
        cached_statuses={"notes": "healthy"},
    )
    assert failed["installedServices"][0]["status"] == "error"


@pytest.mark.parametrize(
    ("system", "machine", "runtime", "gpu", "code"),
    [
        ("Plan9", "x86_64", "docker", "cpu", "unsupported-host-platform"),
        ("Linux", "mips", "docker", "cpu", "unsupported-host-architecture"),
        ("Linux", "x86_64", "containerd", "cpu", "unsupported-container-runtime"),
        ("Linux", "x86_64", "docker", "mystery", "unsupported-gpu-backend"),
    ],
)
def test_observed_state_fails_closed_for_unknown_host_facts(
    monkeypatch, tmp_path, system, machine, runtime, gpu, code
):
    monkeypatch.setattr(production.platform, "system", lambda: system)
    monkeypatch.setattr(production.platform, "machine", lambda: machine)
    monkeypatch.setattr(production, "GPU_BACKEND", gpu)
    monkeypatch.setenv("ODS_CONTAINER_RUNTIME", runtime)

    with pytest.raises(PlanningError, match=code):
        production.production_observed_state(
            install_dir=tmp_path,
            data_dir=tmp_path,
            user_extensions_dir=tmp_path / "user",
            builtin_extensions_dir=tmp_path / "builtin",
            catalog_entries=[],
            cached_statuses={},
        )


def test_production_runtime_wires_configuration_but_not_execution(monkeypatch):
    captured = {}

    class Store:
        def __init__(self, root):
            captured["root"] = root

    class Custodian:
        pass

    class Configuration:
        def __init__(self, store, custodian, clock):
            captured["configuration"] = (store, custodian, clock)

    monkeypatch.setattr(production, "TransactionStore", Store)
    monkeypatch.setattr(production, "HostSecretCustodian", Custodian)
    monkeypatch.setattr(production, "TransactionConfigurationManager", Configuration)
    monkeypatch.setattr(production, "DATA_DIR", "/var/lib/ods-test")

    runtime = production.create_production_runtime()

    assert captured["root"].as_posix() == "/var/lib/ods-test/assistant-first/transaction-store"
    assert captured["configuration"][0] is runtime.store
    assert isinstance(captured["configuration"][1], Custodian)
    assert runtime.configuration is not None
    assert runtime.executor is None


def test_production_runtime_feature_gate(monkeypatch):
    monkeypatch.delenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", raising=False)
    assert production.production_runtime_enabled() is False
    monkeypatch.setenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "TRUE")
    assert production.production_runtime_enabled() is True
