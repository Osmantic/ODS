"""runtime.placement: read defensively from the host agent, shown by /api/models."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from model_placement import is_unintended_cpu_placement, runtime_placement
from models import GPUInfo

# The contract fields every host sends.
LAPTOP_PARTIAL = {
    "layersOnGpu": 29,
    "layersTotal": 33,
    "cpuWeightMiB": 1078.96,
    "fullyResident": False,
    "intentionalOffload": False,
}
RESIDENT = {
    "layersOnGpu": 33,
    "layersTotal": 33,
    "cpuWeightMiB": 545.62,
    "fullyResident": True,
    "intentionalOffload": False,
}
# The host agent's full record adds a status, a reason and diagnostics.
HOST_PARTIAL = {
    **LAPTOP_PARTIAL,
    "schema": "ods.model-placement.v1",
    "source": "llama-server-log",
    "modelFile": "Qwen3.5-9B-Q4_K_M.gguf",
    "gpuWeightMiB": 4327.95,
    "cpuKvMiB": 136.0,
    "status": "partial",
    "reason": "29/33 layers on the GPU, 136 MiB of KV cache in system memory",
}
HOST_UNVERIFIED = {
    "schema": "ods.model-placement.v1",
    "layersOnGpu": None,
    "layersTotal": None,
    "cpuWeightMiB": None,
    "fullyResident": None,
    "intentionalOffload": False,
    "status": "unverified",
    "reason": "The runtime log does not report layer placement for this model.",
}


def _status(placement):
    return {"status": "idle", "runtime": {"placement": placement}}


def _expected(placement, state, detail=None):
    fields = ("layersOnGpu", "layersTotal", "cpuWeightMiB", "fullyResident", "intentionalOffload")
    return {**{key: placement[key] for key in fields}, "state": state, "detail": detail}


def test_contract_fields_derive_the_display_state():
    assert runtime_placement(_status(LAPTOP_PARTIAL)) == _expected(LAPTOP_PARTIAL, "partial")
    assert runtime_placement(_status(RESIDENT)) == _expected(RESIDENT, "resident")
    assert runtime_placement(_status({**RESIDENT, "intentionalOffload": True}))["state"] == "intentional"
    cpu_only = {**LAPTOP_PARTIAL, "layersOnGpu": 0}
    assert runtime_placement(_status(cpu_only))["state"] == "cpu_only"


def test_a_declaration_never_excuses_layers_on_the_cpu():
    placement = runtime_placement(_status({**LAPTOP_PARTIAL, "intentionalOffload": True}))
    assert placement["state"] == "partial"
    assert is_unintended_cpu_placement(placement)


def test_host_status_and_reason_are_carried():
    assert runtime_placement(_status(HOST_PARTIAL)) == _expected(
        LAPTOP_PARTIAL, "partial", HOST_PARTIAL["reason"],
    )
    moe = {**RESIDENT, "layersOnGpu": 49, "layersTotal": 49, "intentionalOffload": True,
           "status": "intentional_offload", "reason": "MoE expert weights are kept in system memory."}
    assert runtime_placement(_status(moe))["state"] == "intentional"


@pytest.mark.parametrize("status", [
    None,
    "idle",
    {"status": "idle"},
    {"status": "idle", "runtime": None},
    {"status": "idle", "runtime": {}},
    {"status": "idle", "runtime": {"placement": None}},
    _status(HOST_UNVERIFIED),
])
def test_hosts_without_a_verified_placement_report_unknown(status, caplog):
    assert runtime_placement(status) is None
    assert "malformed" not in caplog.text


@pytest.mark.parametrize("placement", [
    "29/33",
    {**LAPTOP_PARTIAL, "layersOnGpu": "29"},
    {**LAPTOP_PARTIAL, "layersOnGpu": True},
    {**LAPTOP_PARTIAL, "layersOnGpu": -1},
    {**LAPTOP_PARTIAL, "layersTotal": 0},
    {**LAPTOP_PARTIAL, "layersOnGpu": 34},
    {**LAPTOP_PARTIAL, "fullyResident": "false"},
    {**LAPTOP_PARTIAL, "intentionalOffload": "no"},
    # A "fully resident" claim with layers on the CPU contradicts itself.
    {**LAPTOP_PARTIAL, "fullyResident": True},
    {key: value for key, value in LAPTOP_PARTIAL.items() if key != "fullyResident"},
    # A status must agree with fullyResident and be one the dashboard knows.
    {**HOST_PARTIAL, "status": "fully_resident"},
    {**RESIDENT, "status": "partial"},
    {**RESIDENT, "status": "mostly_fine"},
])
def test_malformed_placement_is_unknown_not_fitting(placement, caplog):
    assert runtime_placement(_status(placement)) is None
    assert "malformed" in caplog.text


def test_optional_fields_are_normalized():
    placement = {key: value for key, value in LAPTOP_PARTIAL.items() if key != "intentionalOffload"}
    placement["cpuWeightMiB"] = float("nan")
    placement["reason"] = "   "
    assert runtime_placement(_status(placement)) == {
        **_expected(LAPTOP_PARTIAL, "partial"),
        "cpuWeightMiB": None,
    }
    long_reason = runtime_placement(_status({**HOST_PARTIAL, "reason": "x" * 5000}))["detail"]
    assert len(long_reason) == 400


def test_only_undeclared_cpu_placement_counts_as_unintended():
    assert is_unintended_cpu_placement(runtime_placement(_status(LAPTOP_PARTIAL)))
    assert is_unintended_cpu_placement(runtime_placement(_status({**LAPTOP_PARTIAL, "layersOnGpu": 0})))
    assert not is_unintended_cpu_placement(runtime_placement(_status(RESIDENT)))
    assert not is_unintended_cpu_placement(runtime_placement(_status({**RESIDENT, "intentionalOffload": True})))
    assert not is_unintended_cpu_placement(None)


def _patch_router(monkeypatch, tmp_path, agent_status, tokens_per_second=0.0, gpu=True):
    import helpers
    import routers.models as models_router

    install_dir = tmp_path / "ods"
    data_dir = install_dir / "data"
    (data_dir / "models").mkdir(parents=True)
    (install_dir / ".env").write_text("ODS_MODE=local\n", encoding="utf-8")
    (install_dir / "config").mkdir()
    (install_dir / "config" / "model-library.json").write_text(json.dumps({"version": 2, "models": [{
        "id": "qwen3.5-9b-q4",
        "name": "Qwen 3.5 9B",
        "gguf_file": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_mb": 5760,
        "vram_required_gb": 8,
        "context_length": 65536,
        "quantization": "Q4_K_M",
        "specialty": "General",
        "description": "Balanced default.",
        "llm_model_name": "qwen3.5-9b",
    }]}), encoding="utf-8")
    (data_dir / "models" / "Qwen3.5-9B-Q4_K_M.gguf").write_text("model", encoding="utf-8")
    monkeypatch.setattr(helpers, "_PERF_FILE", data_dir / "model_performance.json")
    monkeypatch.setattr(models_router, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(models_router, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(models_router, "_LIBRARY_PATH", install_dir / "config" / "model-library.json")
    monkeypatch.setattr(models_router, "_MODELS_DIR", data_dir / "models")
    monkeypatch.setattr(models_router, "_ENV_PATH", install_dir / ".env")
    monkeypatch.setattr(models_router, "ODS_MODE_EFFECTIVE", "local")
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: GPUInfo(
        name="NVIDIA GeForce RTX 5070 Laptop GPU",
        memory_used_mb=6228,
        memory_total_mb=8151,
        memory_percent=76.4,
        utilization_percent=0,
        temperature_c=45,
        gpu_backend="nvidia",
    ) if gpu else None)
    monkeypatch.setattr(models_router, "get_loaded_model", AsyncMock(return_value="Qwen3.5-9B-Q4_K_M.gguf"))
    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={
        "tokens_per_second": tokens_per_second,
        "lifetime_tokens": 0,
        "throughput_mode": "generation_interval",
        "throughput_state": "measured",
        "throughput_sampled_at": 1000,
        "throughput_model": "Qwen3.5-9B-Q4_K_M.gguf",
    }))
    monkeypatch.setattr(models_router, "get_llama_context_size", AsyncMock(return_value=65536))
    monkeypatch.setattr(models_router, "SERVICES", {"llama-server": {"host": "localhost", "port": 8080}})
    monkeypatch.setattr(models_router, "_get_agent_model_status", lambda: agent_status)
    monkeypatch.setattr(models_router, "_last_recorded_throughput_sample", None)
    recorded = []
    monkeypatch.setattr(
        models_router,
        "record_model_performance",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )
    return recorded


def test_api_models_exposes_host_placement(test_client, monkeypatch, tmp_path):
    _patch_router(monkeypatch, tmp_path, _status(HOST_PARTIAL))

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json()["runtime"] == {
        "placement": _expected(LAPTOP_PARTIAL, "partial", HOST_PARTIAL["reason"]),
    }


@pytest.mark.parametrize("agent_status", [None, {"status": "idle"}, _status(HOST_UNVERIFIED)])
def test_api_models_reports_unknown_placement_for_older_hosts(test_client, monkeypatch, tmp_path, agent_status):
    _patch_router(monkeypatch, tmp_path, agent_status)

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json()["runtime"] == {"placement": None}


def test_api_models_hides_placement_while_a_switch_is_in_flight(test_client, monkeypatch, tmp_path):
    _patch_router(monkeypatch, tmp_path, {
        **_status(HOST_PARTIAL),
        "lifecycleActive": True,
        "activeOperation": "model_activation",
        "activeTarget": "phi4-mini-q4",
    })

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json()["runtime"] == {"placement": None}


def test_api_models_hides_placement_without_a_gpu(test_client, monkeypatch, tmp_path):
    _patch_router(monkeypatch, tmp_path, _status({**LAPTOP_PARTIAL, "layersOnGpu": 0}), gpu=False)

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json()["runtime"] == {"placement": None}


def test_partial_offload_speed_is_not_recorded_as_gpu_speed(test_client, monkeypatch, tmp_path):
    recorded = _patch_router(monkeypatch, tmp_path, _status(HOST_PARTIAL), tokens_per_second=18.5)

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert recorded == []


@pytest.mark.parametrize("placement", [RESIDENT, {**RESIDENT, "intentionalOffload": True}, None])
def test_resident_or_declared_offload_speed_is_still_recorded(test_client, monkeypatch, tmp_path, placement):
    recorded = _patch_router(monkeypatch, tmp_path, _status(placement), tokens_per_second=45.5)

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert len(recorded) == 1
