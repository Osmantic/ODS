"""runtime.placement: read defensively from the host agent, shown by /api/models."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from model_placement import is_unintended_cpu_placement, is_unproven_gpu_placement, runtime_placement
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
# The same status from a loaded ODS-managed llama-server whose load log the
# host agent read to the end: the model is up, its placement is not stated.
HOST_UNVERIFIED_LOADED = {
    **HOST_UNVERIFIED,
    "runtime": "container",
    "runtimeStartedAt": "2026-09-25T16:01:43.372967237Z",
    "observationComplete": True,
}
# The laptop's fit numbers as the host agent records them (PR #6706).
HOST_LAPTOP_FIT = {
    **HOST_PARTIAL,
    "projectedDeviceMiB": 6492,
    "freeDeviceMiB": 6860,
    "fitTargetMiB": 1024,
    "gpuComputeMiB": 493.0,
    "ubatch": 512,
    "nGpuLayers": "auto",
}


def _status(placement):
    return {"status": "idle", "runtime": {"placement": placement}}


def _expected(placement, state, detail=None, **extra):
    fields = ("layersOnGpu", "layersTotal", "cpuWeightMiB", "fullyResident", "intentionalOffload")
    return {
        **{key: placement[key] for key in fields},
        "cpuKvMiB": placement.get("cpuKvMiB"),
        "overflowingLayers": placement.get("overflowingLayers"),
        "state": state,
        "remedy": None,
        "fitTargetMiB": None,
        "detail": detail,
        **extra,
    }


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
        HOST_PARTIAL, "partial", HOST_PARTIAL["reason"],
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
    # An older agent record without the runtime fields.
    _status(HOST_UNVERIFIED),
    # Lemonade manages placement itself and does not log it.
    _status({**HOST_UNVERIFIED_LOADED, "runtime": "lemonade"}),
    _status({**HOST_UNVERIFIED_LOADED, "runtime": "cpu", "status": "not_applicable"}),
    # No running llama-server log, or a load still in progress.
    _status({**HOST_UNVERIFIED_LOADED, "runtimeStartedAt": ""}),
    _status({**HOST_UNVERIFIED_LOADED, "observationComplete": False}),
])
def test_hosts_without_a_placement_to_judge_report_unknown(status, caplog):
    assert runtime_placement(status) is None
    assert "malformed" not in caplog.text


@pytest.mark.parametrize("runtime", ["container", "native"])
def test_loaded_managed_server_without_placement_is_unverified_not_unknown(runtime):
    placement = runtime_placement(_status({**HOST_UNVERIFIED_LOADED, "runtime": runtime}))
    assert placement == {
        "layersOnGpu": None,
        "layersTotal": None,
        "cpuWeightMiB": None,
        "cpuKvMiB": None,
        "overflowingLayers": None,
        "fullyResident": None,
        "intentionalOffload": False,
        "state": "unverified",
        "remedy": None,
        "fitTargetMiB": None,
        "detail": HOST_UNVERIFIED["reason"],
    }
    assert is_unproven_gpu_placement(placement)
    assert not is_unintended_cpu_placement(placement)


def test_idle_gpu_spill_from_the_fit_margin_gets_the_refit_remedy():
    # The 8 GB laptop: 6492 MiB needed, 6860 MiB free; only llama.cpp's
    # 1024 MiB margin moved layers out.
    placement = runtime_placement(_status(HOST_LAPTOP_FIT))
    assert (placement["remedy"], placement["fitTargetMiB"]) == ("refit", 1024)


@pytest.mark.parametrize("overrides, remedy", [
    # tower1's F5: a leftover container held 20 GB of the 5090.
    ({"projectedDeviceMiB": 20013, "freeDeviceMiB": 10810}, "free_or_shrink"),
    # The refit settings are already in effect and it still does not fit.
    ({"projectedDeviceMiB": 6246, "freeDeviceMiB": 6360, "fitTargetMiB": 512, "ubatch": 256,
      "gpuComputeMiB": 246.5}, "free_or_shrink"),
    ({"nGpuLayers": "20"}, "set_auto_layers"),
    ({"projectedDeviceMiB": None, "freeDeviceMiB": None}, None),
])
def test_spill_remedy_follows_the_fit_numbers(overrides, remedy):
    assert runtime_placement(_status({**HOST_LAPTOP_FIT, **overrides}))["remedy"] == remedy


def test_moe_overflow_and_cpu_kv_are_carried_for_the_message():
    moe = {**HOST_PARTIAL, "layersOnGpu": 49, "layersTotal": 49, "cpuKvMiB": 0.0, "overflowingLayers": 12,
           "reason": "49/49 layers on the GPU, MoE expert weights of 12 layers moved to system memory"}
    placement = runtime_placement(_status(moe))
    assert (placement["state"], placement["overflowingLayers"], placement["cpuKvMiB"]) == ("partial", 12, 0.0)


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
    assert runtime_placement(_status({**HOST_PARTIAL, "overflowingLayers": -3, "cpuKvMiB": "136"})) == _expected(
        HOST_PARTIAL, "partial", HOST_PARTIAL["reason"], cpuKvMiB=None, overflowingLayers=None,
    )
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
        "placement": _expected(HOST_PARTIAL, "partial", HOST_PARTIAL["reason"]),
    }


def test_api_models_exposes_unverified_placement_and_the_remedy(test_client, monkeypatch, tmp_path):
    _patch_router(monkeypatch, tmp_path, _status(HOST_UNVERIFIED_LOADED))
    resp = test_client.get("/api/models", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["runtime"]["placement"]["state"] == "unverified"
    assert resp.json()["runtime"]["placement"]["layersTotal"] is None

    _patch_router(monkeypatch, tmp_path / "refit", _status(HOST_LAPTOP_FIT))
    resp = test_client.get("/api/models", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["runtime"]["placement"]["remedy"] == "refit"


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


@pytest.mark.parametrize("placement", [HOST_PARTIAL, HOST_UNVERIFIED_LOADED])
def test_partial_or_unverified_speed_is_not_recorded_as_gpu_speed(test_client, monkeypatch, tmp_path, placement):
    recorded = _patch_router(monkeypatch, tmp_path, _status(placement), tokens_per_second=18.5)

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert recorded == []


@pytest.mark.parametrize("placement", [RESIDENT, {**RESIDENT, "intentionalOffload": True}, None])
def test_resident_or_declared_offload_speed_is_still_recorded(test_client, monkeypatch, tmp_path, placement):
    recorded = _patch_router(monkeypatch, tmp_path, _status(placement), tokens_per_second=45.5)

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert len(recorded) == 1


def test_reload_activates_the_running_model_again(test_client, monkeypatch, tmp_path):
    """{"reload": true} is the dashboard's "Reload on GPU": a real activation."""
    import routers.models as models_router

    _patch_router(monkeypatch, tmp_path, _status(HOST_LAPTOP_FIT))
    install_dir, data_dir = Path(models_router.INSTALL_DIR), Path(models_router.DATA_DIR)
    (install_dir / ".env").write_text(
        "ODS_MODE=local\n"
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n"
        "CTX_SIZE=65536\n"
        "MAX_CONTEXT=65536\n",
        encoding="utf-8",
    )
    (data_dir / "model-activation-receipt.json").write_text(json.dumps({
        "schema": "ods.model-activation-receipt.v1",
        "status": "complete",
        "modelId": "qwen3.5-9b-q4",
        "ggufFile": "Qwen3.5-9B-Q4_K_M.gguf",
        "runtimeModelId": "Qwen3.5-9B-Q4_K_M.gguf",
        "consumers": {"dashboard": "live_env"},
    }), encoding="utf-8")
    monkeypatch.setattr(models_router, "_fetch_loaded_model_sync", lambda: "Qwen3.5-9B-Q4_K_M.gguf")
    monkeypatch.setattr(models_router, "_loaded_model_backend_ready_sync", lambda _loaded: True)
    calls = []
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda path, body, timeout=30, **kwargs: calls.append((path, body))
        or {"status": "activated", "context_length": body.get("context_length")},
    )
    url = "/api/models/qwen3.5-9b-q4/load"

    noop = test_client.post(url, headers=test_client.auth_headers, json={})
    assert noop.status_code == 200
    assert noop.json()["status"] == "already_active"
    assert calls == []

    for reload_value in ("true", 1):
        # Only a JSON true asks for a reload.
        resp = test_client.post(url, headers=test_client.auth_headers, json={"reload": reload_value})
        assert resp.json()["status"] == "already_active"
    assert calls == []

    resp = test_client.post(url, headers=test_client.auth_headers, json={"reload": True})
    assert resp.status_code == 200
    assert resp.json().get("status") != "already_active"
    assert [path for path, _body in calls] == ["/v1/model/activate"]
    assert calls[0][1]["model_id"] == "qwen3.5-9b-q4"
