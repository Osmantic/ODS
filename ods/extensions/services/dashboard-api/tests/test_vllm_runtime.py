"""The dashboard on a vLLM install (LLM_BACKEND=vllm, docker-compose.vllm.yml).

vllm-server replaces the in-stack llama-server: the LLM row probes it, its
/v1/models row gives the context (no /props), the Models page shows the
installed vLLM catalog model, readiness names vllm-server (and suggests no
restart during a cold start), setup chat names the served model, and the
model lifecycle refuses vLLM entries with a 409 instead of a 500.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import _apply_vllm_service_override
from models import BootstrapStatus, GPUInfo, ServiceStatus

CATALOG = Path(__file__).resolve().parents[4] / "config" / "model-library.json"
SERVED = "GLM-5.3-Flash-NVFP4"


def _vllm_entry(model_id="glm-5.3-flash-nvfp4-vllm"):
    models = json.loads(CATALOG.read_text(encoding="utf-8"))["models"]
    return next(model for model in models if model["id"] == model_id)


# --- config: the LLM row probes vllm-server ---


def test_vllm_route_repoints_the_llm_row_at_vllm_server():
    services = {"llama-server": {"host": "llama-server", "port": 8080, "health": "/health",
                                 "name": "llama-server (LLM Inference)", "container_name": "ods-llama-server"}}
    _apply_vllm_service_override(services, {"LLM_BACKEND": "vllm"})
    assert services["llama-server"] == {
        "host": "vllm-server", "port": 8000, "health": "/health", "name": "vLLM (LLM Inference)",
        "container_name": "ods-vllm-server", "external_link": False,
    }


@pytest.mark.parametrize("backend", ["", "llama-server", "external", "lemonade"])
def test_other_routes_keep_the_llm_row(backend):
    row = {"host": "llama-server", "port": 8080, "health": "/health"}
    services = {"llama-server": dict(row)}
    _apply_vllm_service_override(services, {"LLM_BACKEND": backend})
    assert services["llama-server"] == row


# --- helpers: context from /v1/models, no /props ---


def _client(monkeypatch, payload):
    response = MagicMock()
    response.json = MagicMock(return_value=payload)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr("helpers._get_httpx_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_vllm_context_is_the_served_rows_max_model_len(monkeypatch):
    from helpers import get_llama_context_size, get_llama_vision_support

    monkeypatch.setattr("helpers.SERVICES", {"llama-server": {"host": "vllm-server", "port": 8000}})
    monkeypatch.setattr("helpers.LLM_BACKEND", "vllm")
    monkeypatch.setattr("helpers._host_native_llm", lambda: False)
    client = _client(monkeypatch, {"object": "list", "data": [
        {"id": SERVED, "object": "model", "owned_by": "vllm", "max_model_len": 131072}]})

    assert await get_llama_context_size(model_hint=SERVED) == 131072
    assert await get_llama_context_size(model_hint=None) == 131072
    assert [call.args[0] for call in client.get.call_args_list] == ["http://vllm-server:8000/v1/models"] * 2
    # Another id, or a row without a usable length, reports no context.
    assert await get_llama_context_size(model_hint="Other") is None
    _client(monkeypatch, {"data": [{"id": SERVED, "max_model_len": "131072"}]})
    assert await get_llama_context_size(model_hint=SERVED) is None
    _client(monkeypatch, ["not", "an", "object"])
    assert await get_llama_context_size(model_hint=SERVED) is None
    # vLLM reports no modalities: unknown, and nothing asks for /props.
    client = _client(monkeypatch, {})
    assert await get_llama_vision_support() is None
    client.get.assert_not_called()


VLLM_METRICS = """# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{engine="0",model_name="GLM-5.3-Flash-NVFP4"} 1.0
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{engine="0",model_name="GLM-5.3-Flash-NVFP4"} 4321.0
vllm:generation_tokens_created{engine="0",model_name="GLM-5.3-Flash-NVFP4"} 1.7e9
vllm:prompt_tokens_total{engine="0",model_name="GLM-5.3-Flash-NVFP4"} 9876.0
"""


@pytest.mark.asyncio
async def test_vllm_metrics_count_tokens_without_claiming_a_rate(monkeypatch, tmp_path):
    import helpers

    monkeypatch.setattr("helpers.SERVICES", {"llama-server": {"host": "vllm-server", "port": 8000}})
    monkeypatch.setattr("helpers.LLM_BACKEND", "vllm")
    monkeypatch.setattr("helpers._host_native_llm", lambda: False)
    monkeypatch.setattr(helpers, "_TOKEN_FILE", tmp_path / "token_counter.json")
    helpers._prev_tokens.clear()
    response = MagicMock()
    response.text = VLLM_METRICS
    response.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr("helpers._get_httpx_client", AsyncMock(return_value=client))

    result = await helpers._fetch_llama_metrics(model_hint=SERVED)

    assert result["tokens_per_second"] is None and result["_available"] is False
    assert result["inference_active"] is True
    assert result["_counters"] == (4321.0, None)
    # One /metrics read: vLLM has no llama.cpp /slots to ask while it is busy.
    assert [call.args[0] for call in client.get.call_args_list] == ["http://vllm-server:8000/metrics"]


# --- readiness: names vllm-server, no restart while it starts ---


def _readiness(monkeypatch, status, *, startup_pending=False, backend="vllm"):
    import main

    monkeypatch.setattr(main, "LLM_BACKEND", backend)
    statuses = [
        ServiceStatus(id="llama-server", name="vLLM (LLM Inference)", port=8000, external_port=8000,
                      status=status, startup_pending=startup_pending),
        ServiceStatus(id="open-webui", name="Open WebUI", port=3000, external_port=3000, status="healthy"),
    ]
    result = main._build_readiness_payload(
        service_statuses=statuses, loaded_model=None if status != "healthy" else SERVED,
        context_size=None if status != "healthy" else 131072, bootstrap_info=BootstrapStatus(active=False),
        host_agent={"available": True}, stt_model_cached=None, stt_model_name="whisper")
    return next(check for check in result["checks"] if check["id"] == "chat")


def test_readiness_waits_calmly_through_a_vllm_cold_start(monkeypatch):
    chat = _readiness(monkeypatch, "degraded", startup_pending=True)
    assert chat["detail"].startswith("The vLLM engine is starting")
    assert chat.get("repair") is None

    chat = _readiness(monkeypatch, "down")
    assert chat["detail"] == "vllm-server is not healthy"
    assert chat["repair"] == "ods restart vllm-server"

    chat = _readiness(monkeypatch, "healthy")
    assert chat["ready"] is True and chat["detail"] == f"{SERVED} loaded with 131072 context"


def test_llama_server_readiness_is_unchanged(monkeypatch):
    chat = _readiness(monkeypatch, "degraded", startup_pending=True, backend="llama-server")
    assert chat["detail"] == "llama-server is not healthy"
    assert chat["repair"] == "ods restart llama-server"


# --- Models page and model lifecycle ---


def _vllm_install(monkeypatch, tmp_path, env_text):
    import routers.models as models_router

    install_dir = tmp_path / "ods"
    data_dir = install_dir / "data"
    (data_dir / "models").mkdir(parents=True)
    (install_dir / "config").mkdir()
    (install_dir / "config" / "model-library.json").write_text(CATALOG.read_text(encoding="utf-8"),
                                                              encoding="utf-8")
    (install_dir / ".env").write_text(env_text, encoding="utf-8")
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(models_router, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(models_router, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(models_router, "_LIBRARY_PATH", install_dir / "config" / "model-library.json")
    monkeypatch.setattr(models_router, "_MODELS_DIR", data_dir / "models")
    monkeypatch.setattr(models_router, "_ENV_PATH", install_dir / ".env")
    monkeypatch.setattr(models_router, "ODS_MODE_EFFECTIVE", "local")
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: None)
    monkeypatch.setattr(models_router, "_request_agent_download",
                        lambda payload: pytest.fail("a vLLM snapshot reached the GGUF download"))
    monkeypatch.setattr(models_router, "_request_agent_waiting_short_holds",
                        lambda *args, **kwargs: pytest.fail("a vLLM snapshot reached the GGUF delete"))
    return models_router, install_dir, data_dir


VLLM_ENV = ("ODS_MODE=local\nLLM_BACKEND=vllm\nLLM_MODEL=glm-5.3-flash-nvfp4\n"
            "VLLM_SERVED_MODEL=GLM-5.3-Flash-NVFP4\nGGUF_FILE=\nMAX_CONTEXT=131072\n")


@pytest.mark.parametrize("method,path", [
    ("post", "/api/models/glm-5.3-flash-nvfp4-vllm/load"),
])
def test_vllm_entries_are_refused_with_a_clear_409(test_client, monkeypatch, tmp_path, method, path):
    models_router, _install, _data = _vllm_install(
        monkeypatch, tmp_path, "ODS_MODE=local\nLLM_BACKEND=llama-server\nGGUF_FILE=a.gguf\n")
    monkeypatch.setattr(models_router, "LLM_BACKEND", "llama-server")
    monkeypatch.setattr(models_router, "_configured_ods_mode", lambda: "local")
    monkeypatch.setattr(models_router, "_windows_hosted_runtime", lambda: False)
    response = getattr(test_client, method)(path, headers=test_client.auth_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "vllm_model_installer_managed"
    assert detail["requestedModelId"] == "glm-5.3-flash-nvfp4-vllm"
    assert detail["error"].startswith("GLM 5.3 Flash NVFP4 (vLLM) runs only on the vLLM engine.")
    assert "ODS installer" in detail["error"]


def test_a_vllm_install_refuses_model_switches_before_the_host_agent():
    import routers.models as models_router

    denial = models_router._model_activation_mode_denial("local", "local", "vllm")
    assert denial["code"] == "vllm_runtime_installer_managed"
    assert "vLLM engine" in denial["message"] and "installer" in denial["message"]
    assert models_router._model_activation_mode_denial("local", "local", "llama-server") is None


def _gpu():
    return GPUInfo(name="NVIDIA GB300", memory_used_mb=200_000, memory_total_mb=288_000, memory_percent=69.0,
                   utilization_percent=10, temperature_c=40, gpu_backend="nvidia")


@pytest.mark.parametrize("loaded,status", [(SERVED, "loaded"), (None, "available")])
def test_models_page_shows_the_installed_vllm_model(test_client, monkeypatch, tmp_path, loaded, status):
    models_router, _install, _data = _vllm_install(monkeypatch, tmp_path, VLLM_ENV)
    monkeypatch.setattr(models_router, "get_gpu_info", _gpu)
    monkeypatch.setattr(models_router, "get_loaded_model", AsyncMock(return_value=loaded))
    monkeypatch.setattr(models_router, "_fetch_llama_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0}))
    monkeypatch.setattr(models_router, "get_llama_context_size",
                        AsyncMock(return_value=131072 if loaded else None))
    monkeypatch.setattr(models_router, "LLM_BACKEND", "vllm")
    monkeypatch.setattr(models_router, "record_model_performance",
                        lambda *args, **kwargs: pytest.fail("no measured throughput on vLLM"))
    response = test_client.get("/api/models", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    rows = [row for row in payload["models"] if row["id"] == "glm-5.3-flash-nvfp4-vllm"]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == status
    assert row["name"] == "GLM 5.3 Flash NVFP4 (vLLM)"
    assert row["size"] == "190.4 GB" and row["sizeGb"] == 190.4
    assert row["contextLength"] == 131072 and row["maxContextLength"] == 131072
    assert row["quantization"] == "NVFP4"
    assert row["vramRequired"] == 239.0
    assert row["metadata"]["source"] == "ods" and row["metadata"]["runtime"] == "vllm"
    assert row["metadata"]["servedModelName"] == SERVED
    assert row["gguf"] is None and row["downloadUrl"] is None
    assert payload["configuredModel"] == "glm-5.3-flash-nvfp4-vllm"
    assert payload["llmBackend"] == "vllm"
    assert payload["currentModel"] == ("glm-5.3-flash-nvfp4-vllm" if loaded else None)
    # The served model is the catalog row, never an anonymous runtime row,
    # and no GGUF claims to be the installer's selection.
    assert not [row for row in payload["models"] if row["id"].startswith("runtime-")]
    assert not [row for row in payload["models"] if row["recommended"]]
    # Other catalog snapshots can be downloaded without changing the engine.
    assert "qwen3.8-flash-next-nvfp4-vllm" in {row["id"] for row in payload["models"]}


def test_status_names_the_served_vllm_model_by_its_catalog_name(monkeypatch, tmp_path):
    models_router, _install, _data = _vllm_install(monkeypatch, tmp_path, VLLM_ENV)
    monkeypatch.setattr(models_router, "LLM_BACKEND", "vllm")
    assert models_router.loaded_model_display_name(SERVED) == "GLM 5.3 Flash NVFP4 (vLLM)"
    assert models_router.loaded_model_display_name("Unknown-Model") is None
    # A llama-server route never names a vLLM entry.
    monkeypatch.setattr(models_router, "LLM_BACKEND", "llama-server")
    assert models_router.loaded_model_display_name(SERVED) is None


def test_llama_install_lists_vllm_snapshots_without_claiming_activation(monkeypatch, tmp_path):
    from performance_oracle import build_models_payload

    install = tmp_path / "ods"
    install.mkdir()
    (install / ".env").write_text("LLM_BACKEND=llama-server\nLLM_MODEL=glm-5.3-flash-nvfp4\n", encoding="utf-8")
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    payload = build_models_payload(None, None, 0.0, install, install / "data", None,
                                   catalog=[_vllm_entry()], downloaded_files_override={})
    assert len(payload["models"]) == 1
    assert payload["models"][0]["configured"] is False
    assert payload["models"][0]["activationSupport"]["available"] is False


# --- setup chat ---


@pytest.mark.parametrize("values,url,model", [
    # The installer's gateway route: LiteLLM, then model-router, to vLLM.
    ({"LLM_API_URL": "http://litellm:4000", "ODS_MODEL_SWITCHBOARD": "enabled"},
     "http://litellm:4000/v1", "ods/current"),
    # A stale llama-server URL goes through the gateway, never the off container.
    ({"LLM_API_URL": "http://llama-server:8080", "ODS_MODEL_SWITCHBOARD": "enabled"},
     "http://litellm:4000/v1", "ods/current"),
    # Straight to the engine: the exact served id.
    ({"LLM_API_URL": "http://vllm-server:8000"}, "http://vllm-server:8000/v1", SERVED),
])
def test_setup_chat_reaches_the_vllm_engine(monkeypatch, tmp_path, values, url, model):
    import config
    from setup_chat_route import resolve_chat_route

    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    values = {"LLM_BACKEND": "vllm", "LLM_MODEL": "glm-5.3-flash-nvfp4", "VLLM_SERVED_MODEL": SERVED,
              "LITELLM_KEY": "gateway-key", **values}
    (tmp_path / ".env").write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    actual_url, actual_model, _headers = resolve_chat_route("http://vllm-server:8000")
    assert (actual_url, actual_model) == (url + "/chat/completions", model)


@pytest.mark.parametrize("method,path", [
    ("post", "/api/models/glm-5.3-flash-nvfp4-vllm/download"),
    ("delete", "/api/models/glm-5.3-flash-nvfp4-vllm"),
])
def test_snapshot_actions_forward_only_catalog_identity(test_client, monkeypatch, tmp_path, method, path):
    router, _install, _data = _vllm_install(monkeypatch, tmp_path, VLLM_ENV)
    requests = []
    monkeypatch.setattr(router, "_bootstrap_upgrade_download_conflict", lambda: None)
    monkeypatch.setattr(router, "_request_agent_download", lambda body: requests.append(body) or {"status": "downloading"})
    monkeypatch.setattr(router, "_request_agent_waiting_short_holds", lambda endpoint, body, *a, **kw:
                        requests.append(body) or {"status": "deleted"})
    response = getattr(test_client, method)(path, headers=test_client.auth_headers)
    assert response.status_code == 200
    assert requests == [{"model_id": "glm-5.3-flash-nvfp4-vllm"}]


def test_vllm_download_preserves_bootstrap_conflict(test_client, monkeypatch, tmp_path):
    router, _install, _data = _vllm_install(monkeypatch, tmp_path, VLLM_ENV)
    monkeypatch.setattr(router, "_bootstrap_upgrade_download_conflict", lambda: {"code": "bootstrap_busy"})
    response = test_client.post("/api/models/glm-5.3-flash-nvfp4-vllm/download", headers=test_client.auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "bootstrap_busy"
