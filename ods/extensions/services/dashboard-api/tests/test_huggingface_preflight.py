"""GET /api/models/huggingface/preflight and the import endpoint's gate."""

from __future__ import annotations

import json

import pytest

import hf_gguf_header
from models import GPUInfo

REPO = "org/model-GGUF"
_TYPES_B9014 = {"F32": 0, "F16": 1, "Q8_0": 8, "Q4_K": 12, "Q6_K": 14}
POLICY = {
    "backendBuilds": {"nvidia": "b11429", "cpu": "b9014", "amd": "b9014"},
    "builds": {
        "b9014": {"commit": "1" * 40, "architectures": ["llama", "qwen3"], "tensorTypes": _TYPES_B9014},
        "b11429": {"commit": "2" * 40, "architectures": ["llama", "nanbeige", "qwen3"],
                   "tensorTypes": {**_TYPES_B9014, "Q2_0": 42}},
    },
}
GIB = 1024 ** 3


def _details(**overrides):
    details = {
        "id": REPO,
        "sha": "c" * 40,
        "contextLength": 8192,
        "contextSource": "hub_config",
        "license": "apache-2.0",
        "url": f"https://huggingface.co/{REPO}",
        "private": False,
        "gated": False,
        "pipelineTag": "text-generation",
        "tags": ["conversational"],
        "ggufArchitecture": "llama",
        "runtimeCompatible": True,
        "runtimeReason": None,
        "artifacts": [
            {
                "id": "a" * 20,
                "label": "model-Q4_K_M.gguf",
                "quantization": "Q4_K_M",
                "sizeBytes": 4 * GIB,
                "files": [{"filename": "model-Q4_K_M.gguf", "sizeBytes": 4 * GIB, "sha256": "e" * 64}],
                "installed": False,
            },
            {
                "id": "b" * 20,
                "label": "model-Q8_0.gguf",
                "quantization": "Q8_0",
                "sizeBytes": 30 * GIB,
                "files": [{"filename": "model-Q8_0.gguf", "sizeBytes": 30 * GIB, "sha256": "f" * 64}],
                "installed": False,
            },
        ],
    }
    details.update(overrides)
    return details


def _dense_header(architecture="llama", metadata=None):
    meta = {
        "general.architecture": architecture,
        "tokenizer.chat_template": "{% if tools %}<tool_call>{% endif %}{% if enable_thinking %}{% endif %}",
    }
    meta.update(metadata or {})
    return {
        "architecture": architecture, "context_length": 131072, "block_count": 32,
        "embedding_length": 4096, "attention_head_count": 32, "attention_head_count_kv": 8,
        "attention_key_length": 128, "attention_value_length": 128,
        "full_attention_interval": None, "ssm_conv_kernel": None, "ssm_inner_size": None,
        "ssm_state_size": None, "ssm_group_count": None, "ssm_time_step_rank": None,
        "bytes_read": 2_000_000, "metadata": meta,
    }


@pytest.fixture
def preflight_env(monkeypatch, tmp_path):
    import routers.models as models_router

    policy_path = tmp_path / "llama-cpp-architectures.json"
    policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
    monkeypatch.setattr(models_router, "_ARCHITECTURES_PATH", policy_path)
    monkeypatch.setattr(models_router, "GPU_BACKEND", "nvidia")
    monkeypatch.setattr(models_router, "_windows_hosted_runtime", lambda: False)
    monkeypatch.setattr(models_router, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models_router, "_bootstrap_upgrade_download_conflict", lambda: None)
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: GPUInfo(
        name="NVIDIA GeForce RTX 5070 Laptop GPU", memory_used_mb=0, memory_total_mb=8192,
        memory_percent=0.0, utilization_percent=0, temperature_c=40, gpu_backend="nvidia",
    ))
    state = {"details": _details(), "header": _dense_header(), "storage": {
        "freeBytes": 20 * GIB, "totalBytes": 100 * GIB, "marginBytes": 5 * GIB}}
    reads = []
    cached_reads = []

    async def fake_details(repo_id):
        assert repo_id == REPO
        return state["details"]

    async def fake_header(repo_id, revision, filename, *, expected_size=None, token="", client=None):
        reads.append((repo_id, revision, filename, expected_size))
        header = state.get("headers", {}).get(filename, state["header"])
        if isinstance(header, Exception):
            raise header
        return header

    async def fake_storage():
        return state["storage"]

    def fake_cached(repo_id, revision, filename):
        # What the preflight read earlier in this process (the import never reads).
        cached_reads.append((repo_id, revision, filename))
        if "cached_headers" in state:
            return state["cached_headers"].get(filename)
        if not state.get("cached", True) or isinstance(state["header"], Exception):
            return None
        return state["header"] if filename == "model-Q4_K_M.gguf" else None

    monkeypatch.setattr(models_router, "_hf_repo_details", fake_details)
    monkeypatch.setattr(hf_gguf_header, "fetch_gguf_header", fake_header)
    monkeypatch.setattr(hf_gguf_header, "cached_gguf_header", fake_cached)
    monkeypatch.setattr(models_router, "_model_storage_status", fake_storage)
    state["reads"] = reads
    state["cached_reads"] = cached_reads
    return models_router, state


def _preflight(test_client):
    return test_client.get(f"/api/models/huggingface/preflight/{REPO}", headers=test_client.auth_headers)


def test_preflight_reports_header_runtime_fit_template_and_disk(test_client, preflight_env):
    _router, state = preflight_env

    response = _preflight(test_client)

    assert response.status_code == 200
    body = response.json()
    # One network header read, scoped to its artifact rather than the repository.
    assert state["reads"] == [(REPO, "c" * 40, "model-Q4_K_M.gguf", 4 * GIB)]
    assert body["header"] == {"status": "read", "file": "model-Q4_K_M.gguf", "bytesRead": 2_000_000}
    assert body["architecture"] == "llama"
    assert body["runtime"] == {"key": "nvidia", "build": "b11429", "architectureSupported": True}
    assert body["modelKind"] == "chat"
    assert body["contextLength"] == 131072
    assert body["template"] == {"present": True, "tools": True, "thinking": "toggle"}
    assert body["refusal"] is None
    assert body["artifactId"] == "a" * 20
    small, large = body["artifacts"]["a" * 20], body["artifacts"]["b" * 20]
    assert small["fit"]["estimate"] == "architecture"
    assert small["fit"]["status"] in {"fits", "fits_short_context"}
    assert large["fit"]["status"] == "too_large"
    assert large["fit"]["estimate"] == "rough"
    assert large["architecture"] is None
    assert large["template"] == {"present": None, "tools": None, "thinking": "unknown"}
    assert large["contextLength"] is None
    assert large["contextSource"] == "unavailable"
    assert small["disk"] == "ok"
    assert large["disk"] == "insufficient"


def test_preflight_refuses_an_architecture_this_build_lacks(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "GPU_BACKEND", "amd")
    state["header"] = _dense_header("nanbeige")

    body = _preflight(test_client).json()

    assert body["runtime"] == {"key": "amd", "build": "b9014", "architectureSupported": False}
    assert body["refusal"]["code"] == "runtime_architecture_unsupported"
    assert body["refusal"]["overridable"] is True
    assert "b9014" in body["refusal"]["message"]


def test_preflight_names_a_reranker_from_its_header(test_client, preflight_env):
    _router, state = preflight_env
    # The live Qwen3-Reranker GGUF is tagged conversational and text-ranking
    # only on the Hub; its header settles it.
    state["details"] = _details(ggufArchitecture="qwen3")
    state["header"] = _dense_header("qwen3", {"qwen3.pooling_type": 4})

    body = _preflight(test_client).json()

    assert body["modelKind"] == "reranker"
    assert body["refusal"]["code"] == "not_a_chat_model:reranker"
    assert body["refusal"]["overridable"] is False
    assert body["runtime"]["architectureSupported"] is None


def test_preflight_without_a_header_cannot_bind_a_multi_artifact_hub_hint(test_client, preflight_env):
    _router, state = preflight_env
    state["header"] = hf_gguf_header.HeaderUnavailable("rate_limited", "Hugging Face rate limit reached")
    state["storage"] = None

    body = _preflight(test_client).json()

    assert body["header"]["status"] == "unavailable"
    assert body["header"]["reason"] == "rate_limited"
    assert body["architecture"] is None
    assert body["runtime"]["architectureSupported"] is None
    assert body["template"] == {"present": None, "tools": None, "thinking": "unknown"}
    assert body["artifacts"]["a" * 20]["fit"]["estimate"] == "rough"
    assert body["artifacts"]["a" * 20]["disk"] == "unknown"
    assert body["refusal"] is None


def test_preflight_reports_where_the_context_came_from(test_client, preflight_env):
    _router, state = preflight_env

    assert _preflight(test_client).json()["contextSource"] == "gguf_header"

    state["header"] = hf_gguf_header.HeaderUnavailable("rate_limited", "Hugging Face rate limit reached")
    body = _preflight(test_client).json()
    assert body["contextLength"] is None
    assert body["contextSource"] == "unavailable"


_GATED = hf_gguf_header.HeaderUnavailable("gated", "This repository is gated")


@pytest.mark.parametrize("token, header", [
    ("", _GATED),                     # no token: the Hub will not serve the files
    ("test-read-token", _GATED),      # a token whose account has not accepted the license
])
def test_preflight_refuses_a_gated_repository_this_host_cannot_download(
    test_client, preflight_env, monkeypatch, token, header,
):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "_hf_token", lambda: token)
    state["details"] = _details(gated=True)
    state["header"] = header

    body = _preflight(test_client).json()

    assert body["refusal"]["code"] == "gated"
    assert body["refusal"]["overridable"] is False
    assert "accept its license" in body["refusal"]["message"]
    assert "HF_TOKEN in Settings" in body["refusal"]["message"]
    assert all(row["refusal"]["code"] == "gated" for row in body["artifacts"].values())


def test_preflight_allows_a_gated_repository_the_token_can_read(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "_hf_token", lambda: "test-read-token")
    state["details"] = _details(gated=True)

    body = _preflight(test_client).json()

    assert body["header"]["status"] == "read"
    assert body["refusal"] is None


def test_imatrix_calibration_files_are_not_offered_as_models():
    import routers.models as models_router

    # bartowski publishes the quantization's importance matrix next to the weights.
    assert models_router._hf_supported_gguf_filename("Nanbeige_Nanbeige4.2-3B-imatrix.gguf") is False
    assert models_router._hf_supported_gguf_filename("imatrix_unsloth.gguf") is False
    assert models_router._hf_supported_gguf_filename("Nanbeige_Nanbeige4.2-3B-Q4_K_M.gguf") is True


def test_speculative_decoding_heads_are_not_offered_or_read_as_the_model():
    import routers.models as models_router

    # ggml-org/gpt-oss-20b-GGUF and unsloth/gemma-4-E4B-it-GGUF as listed on 2026-10-09.
    for name in ("eagle3-gpt-oss-20b-Q8_0.gguf", "eagle3-gpt-oss-20b-BF16.gguf",
                 "MTP/mtp-gemma-4-E4B-it-Q8_0.gguf", "Qwen3-8B-DFlash-Q8_0.gguf"):
        assert models_router._hf_supported_gguf_filename(name) is False, name
    for name in ("gpt-oss-20b-MXFP4.gguf", "gemma-4-E4B-it-Q4_K_M.gguf", "Smtp-Assistant-7B-Q4_K_M.gguf"):
        assert models_router._hf_supported_gguf_filename(name) is True, name

    sha = "a" * 64
    payload = {"siblings": [
        {"rfilename": name, "lfs": {"size": size, "sha256": sha}}
        for name, size in (("eagle3-gpt-oss-20b-Q8_0.gguf", 921488000), ("gpt-oss-20b-MXFP4.gguf", 12109566624))
    ]}
    artifacts = models_router._hf_gguf_artifacts(payload)
    assert [artifact["label"] for artifact in artifacts] == ["gpt-oss-20b-MXFP4.gguf"]
    # The default checked artifact is the model, never its speculative head.
    assert models_router._preflight_header_artifact(artifacts)["files"][0]["filename"] == "gpt-oss-20b-MXFP4.gguf"


def _import(test_client, **extra):
    return test_client.post(
        "/api/models/huggingface/import",
        headers=test_client.auth_headers,
        json={"repoId": REPO, "artifactId": "a" * 20, **extra},
    )


def test_import_refuses_an_unsupported_architecture_before_any_write(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "GPU_BACKEND", "amd")
    state["header"] = _dense_header("nanbeige")
    monkeypatch.setattr(models_router, "_call_agent_model", lambda *_a, **_k: pytest.fail("not dispatched"))

    response = _import(test_client)

    assert response.status_code == 422
    assert response.headers["X-ODS-Import-Started"] == "false"
    assert response.json()["detail"]["code"] == "runtime_architecture_unsupported"
    assert response.json()["detail"]["overridable"] is True
    assert not (models_router.Path(models_router.DATA_DIR) / "model-imports.json").exists()


def test_import_anyway_records_the_acknowledged_runtime(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "GPU_BACKEND", "amd")
    state["header"] = _dense_header("nanbeige")
    dispatched = []

    def dispatch(_path, payload, **_kwargs):
        dispatched.append(payload)
        return {"status": "started"}

    monkeypatch.setattr(models_router, "_call_agent_model", dispatch)

    response = _import(test_client, allowUnsupportedRuntime=True)

    assert response.status_code == 200
    assert len(dispatched) == 1
    registry = json.loads((models_router.Path(models_router.DATA_DIR) / "model-imports.json").read_text(encoding="utf-8"))
    override = registry["models"][0]["runtime_override"]
    assert override["code"] == "runtime_architecture_unsupported"
    assert override["architecture"] == "nanbeige"
    assert override["build"] == "b9014"


def test_import_anyway_cannot_import_a_reranker_as_a_chat_model(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    state["header"] = _dense_header("qwen3", {"qwen3.pooling_type": 4})
    monkeypatch.setattr(models_router, "_call_agent_model", lambda *_a, **_k: pytest.fail("not dispatched"))

    response = _import(test_client, allowUnsupportedRuntime=True)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "not_a_chat_model:reranker"


def test_import_record_carries_the_header_layout_and_context(test_client, preflight_env, monkeypatch):
    models_router, _state = preflight_env
    monkeypatch.setattr(models_router, "_call_agent_model", lambda path, payload, **_kwargs: {"status": "started"})

    response = _import(test_client)

    assert response.status_code == 200
    registry = json.loads((models_router.Path(models_router.DATA_DIR) / "model-imports.json").read_text(encoding="utf-8"))
    record = registry["models"][0]
    assert record["architecture"] == "llama"
    assert record["block_count"] == 32
    assert record["attention_head_count_kv"] == 8
    assert record["recurrent_state_bytes"] == 0
    assert record["max_context_length"] == 131072
    assert record["context_source"] == "gguf_header"
    assert record["context_length"] == 32768
    assert record["template_signals"] == {"present": True, "tools": True, "thinking": "toggle"}
    assert "runtime_override" not in record


def test_search_rows_flag_text_ranking_repositories():
    import routers.models as models_router

    item = models_router._hf_search_item({
        "id": "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF",
        "pipeline_tag": "text-ranking",
        "tags": ["gguf", "text-ranking", "conversational"],
        "siblings": [{"rfilename": "qwen3-reranker-0.6b-q8_0.gguf"}],
    })

    assert item["runtimeCompatible"] is False


def test_import_never_waits_on_a_hub_header_read(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "_call_agent_model", lambda path, payload, **_kwargs: {"status": "started"})

    response = _import(test_client)

    assert response.status_code == 200
    assert state["reads"] == []


def test_import_accepts_the_checked_repository_revision(test_client, preflight_env, monkeypatch):
    router, state = preflight_env
    monkeypatch.setattr(router, "_call_agent_model", lambda *_a, **_k: {"status": "started"})

    response = _import(test_client, revision="c" * 40)

    assert response.status_code == 200
    assert state["reads"] == []
    record = json.loads((router.Path(router.DATA_DIR) / "model-imports.json").read_text())["models"][0]
    assert record["source_revision"] == "c" * 40


def test_import_rejects_changed_revision_before_cache_registration_or_dispatch(test_client, preflight_env, monkeypatch):
    router, state = preflight_env
    monkeypatch.setattr(router, "_call_agent_model", lambda *_a, **_k: pytest.fail("not dispatched"))

    response = _import(test_client, revision="d" * 40, allowUnsupportedRuntime=True)

    assert response.status_code == 409
    assert response.headers["X-ODS-Import-Started"] == "false"
    assert "revision changed" in response.json()["detail"]
    assert state["reads"] == state["cached_reads"] == []
    assert not (router.Path(router.DATA_DIR) / "model-imports.json").exists()


def test_import_without_a_cached_header_gates_on_the_hub_summary(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "GPU_BACKEND", "amd")
    state["cached"] = False
    state["details"] = _details(ggufArchitecture="nanbeige")
    state["details"]["artifacts"] = state["details"]["artifacts"][:1]
    monkeypatch.setattr(models_router, "_call_agent_model", lambda *_a, **_k: pytest.fail("not dispatched"))

    response = _import(test_client)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "runtime_architecture_unsupported"
    assert state["reads"] == []


def test_selected_artifact_preflight_reads_only_its_own_header(test_client, preflight_env):
    _router, state = preflight_env
    other = _dense_header("qwen3", {"tokenizer.chat_template": "plain prompt"})
    other.update(context_length=4096, block_count=24, attention_head_count_kv=4)
    state["headers"] = {"model-Q8_0.gguf": other}

    response = test_client.get(
        f"/api/models/huggingface/preflight/{REPO}?artifactId={'b' * 20}",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert state["reads"] == [(REPO, "c" * 40, "model-Q8_0.gguf", 30 * GIB)]
    assert body["artifactId"] == "b" * 20
    assert body["architecture"] == body["artifacts"]["b" * 20]["architecture"] == "qwen3"
    assert body["contextLength"] == 4096
    assert body["template"]["tools"] is False
    assert body["artifacts"]["a" * 20]["architecture"] == "llama"


def test_unknown_artifact_preflight_does_not_read_a_different_file(test_client, preflight_env):
    _router, state = preflight_env
    response = test_client.get(
        f"/api/models/huggingface/preflight/{REPO}?artifactId={'f' * 20}",
        headers=test_client.auth_headers,
    )
    assert response.status_code == 409
    assert state["reads"] == []


def test_selected_split_artifact_reads_its_first_part_only(test_client, preflight_env):
    _router, state = preflight_env
    artifact = state["details"]["artifacts"][1]
    artifact["files"] = [
        {"filename": f"other/model-{part:05d}-of-00002.gguf", "sizeBytes": 15 * GIB, "sha256": "f" * 64}
        for part in (1, 2)
    ]

    response = test_client.get(
        f"/api/models/huggingface/preflight/{REPO}?artifactId={'b' * 20}",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 200
    assert state["reads"] == [(REPO, "c" * 40, "other/model-00001-of-00002.gguf", 15 * GIB)]
    assert response.json()["artifactId"] == "b" * 20


def test_mixed_repository_import_uses_selected_cached_header(test_client, preflight_env, monkeypatch):
    router, state = preflight_env
    other = _dense_header("qwen3", {"tokenizer.chat_template": "plain prompt"})
    other.update(context_length=4096, block_count=24, attention_head_count_kv=4)
    state["cached_headers"] = {"model-Q4_K_M.gguf": _dense_header(), "model-Q8_0.gguf": other}
    monkeypatch.setattr(router, "_call_agent_model", lambda *_a, **_k: {"status": "started"})

    response = _import(test_client, artifactId="b" * 20)

    assert response.status_code == 200
    assert state["reads"] == []
    assert state["cached_reads"] == [(REPO, "c" * 40, "model-Q8_0.gguf")]
    record = json.loads((router.Path(router.DATA_DIR) / "model-imports.json").read_text())["models"][0]
    assert record["architecture"] == "qwen3"
    assert record["block_count"] == 24
    assert record["attention_head_count_kv"] == 4
    assert record["context_length"] == record["max_context_length"] == 4096
    assert record["template_signals"]["tools"] is False


def test_unread_selected_artifact_does_not_inherit_a_cached_reranker(test_client, preflight_env, monkeypatch):
    router, state = preflight_env
    state["header"] = _dense_header("qwen3", {"qwen3.pooling_type": 4})
    monkeypatch.setattr(router, "_call_agent_model", lambda *_a, **_k: {"status": "started"})

    body = _preflight(test_client).json()
    assert body["artifacts"]["a" * 20]["refusal"]["code"] == "not_a_chat_model:reranker"
    assert body["artifacts"]["b" * 20]["refusal"] is None
    state["reads"].clear()
    response = _import(test_client, artifactId="b" * 20)

    assert response.status_code == 200
    assert state["reads"] == []
    record = json.loads((router.Path(router.DATA_DIR) / "model-imports.json").read_text())["models"][0]
    assert record["context_length"] == 8192
    assert record.get("max_context_length") is None
    assert record.get("context_source") == "unavailable"
    for key in ("architecture", "block_count", "recurrent_state_bytes", "template_signals"):
        assert key not in record


@pytest.mark.parametrize("field,value", [("repoId", "other/repo"), ("revision", "d" * 40), ("artifactId", "b" * 20), ("file", "other.gguf")])
def test_import_record_ignores_header_bound_to_another_artifact(preflight_env, field, value):
    import asyncio

    router, state = preflight_env
    artifact = state["details"]["artifacts"][0]
    gate = asyncio.run(router._hf_preflight_gate(state["details"], artifact, read_header=False))
    gate["artifact"][field] = value

    record = router._hf_import_record(state["details"], artifact, gate=gate)

    assert "architecture" not in record
    assert "template_signals" not in record
    assert "block_count" not in record
    assert record["context_length"] == 8192


def test_storage_check_allows_for_a_windows_managed_model_store(monkeypatch):
    import asyncio

    import routers.models as models_router

    seen = []

    def fake_agent(method, path, *, timeout):
        seen.append((method, path, timeout))
        return {"freeBytes": 9, "totalBytes": 10, "marginBytes": 1, "extra": "dropped"}

    monkeypatch.setattr(models_router, "request_agent_json", fake_agent)

    status = asyncio.run(models_router._model_storage_status())

    assert status == {"freeBytes": 9, "totalBytes": 10, "marginBytes": 1}
    # Strix Halo's WSL agent needs ~5 s to resolve its Windows model store;
    # the old 5 s budget dropped the disk check there on every preflight.
    assert seen == [("GET", "/v1/model/storage", models_router._MODEL_STORAGE_TIMEOUT_SECONDS)]
    assert models_router._MODEL_STORAGE_TIMEOUT_SECONDS >= 15


def _busy(operation):
    from fastapi import HTTPException

    return HTTPException(status_code=409, detail={
        "error": f"Cannot start model download while {operation} is in progress",
        "code": "model_lifecycle_busy", "activeOperation": operation, "activeTarget": "x.gguf"})


def test_import_waits_out_a_short_lifecycle_hold(test_client, preflight_env, monkeypatch):
    models_router, _state = preflight_env
    calls = []

    def agent(path, payload, **_kwargs):
        calls.append(path)
        if len(calls) < 3:
            # The integrity check a restarted agent runs, then a Pixel re-proof.
            raise _busy("artifact_verification" if len(calls) == 1 else "pixel_access_mode")
        return {"status": "started"}

    monkeypatch.setattr(models_router, "_call_agent_model", agent)
    monkeypatch.setattr(models_router.time, "sleep", lambda _seconds: None)

    response = _import(test_client)

    assert response.status_code == 200
    assert calls == ["/v1/model/download"] * 3


def test_a_busy_host_refuses_the_import_in_words_without_starting_it(test_client, preflight_env, monkeypatch):
    models_router, _state = preflight_env
    calls = []

    def agent(path, payload, **_kwargs):
        calls.append(path)
        raise _busy("model_activation")

    monkeypatch.setattr(models_router, "_call_agent_model", agent)

    response = _import(test_client)

    assert response.status_code == 409
    assert response.headers["X-ODS-Import-Started"] == "false"
    assert response.json()["detail"]["message"] == (
        "ODS is switching models right now, so this download cannot start yet. Try again in a minute.")
    assert len(calls) == 1  # a model switch is not a short hold


def test_a_short_hold_that_outlasts_the_grace_still_ends_in_words(test_client, preflight_env, monkeypatch):
    models_router, _state = preflight_env
    clock = [1000.0]

    def agent(path, payload, *, timeout):
        clock[0] += min(20.0, timeout)
        raise _busy("artifact_verification")

    monkeypatch.setattr(models_router, "_call_agent_model", agent)
    monkeypatch.setattr(models_router.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(models_router.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    response = _import(test_client)

    assert response.status_code == 409
    assert "checking a downloaded model file" in response.json()["detail"]["message"]
    assert clock[0] == 1030.0
    assert response.headers["X-ODS-Import-Started"] == "false"


def _two_quantizations():
    return _details(artifacts=[
        {
            "id": "a" * 20, "label": "model-Q4_K_M.gguf", "quantization": "Q4_K_M", "sizeBytes": 4 * GIB,
            "files": [{"filename": "model-Q4_K_M.gguf", "sizeBytes": 4 * GIB, "sha256": "e" * 64}],
            "installed": False,
        },
        {
            "id": "c" * 20, "label": "model-Q2_0.gguf", "quantization": "Q2_0", "sizeBytes": 5 * GIB,
            "files": [{"filename": "model-Q2_0.gguf", "sizeBytes": 5 * GIB, "sha256": "d" * 64}],
            "installed": False,
        },
    ])


@pytest.mark.parametrize("backend, q2_status", [("amd", "unsupported"), ("nvidia", "ok")])
def test_preflight_judges_each_files_tensor_types(test_client, preflight_env, monkeypatch, backend, q2_status):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "GPU_BACKEND", backend)
    state["details"] = _two_quantizations()
    state["header"] = {**_dense_header(), "tensor_types": [0, 12, 14]}

    body = _preflight(test_client).json()

    # The header read was the smallest file's: it speaks for that file only.
    assert body["artifacts"]["a" * 20]["tensors"] == {"status": "ok", "unknown": [], "source": "header"}
    q2 = body["artifacts"]["c" * 20]["tensors"]
    assert (q2["status"], q2["source"]) == (q2_status, "name")
    assert body["refusal"] is None  # the repository itself is fine
    if q2_status == "unsupported":
        assert q2["unknown"] == ["Q2_0"]
        assert q2["refusal"]["code"] == "runtime_tensor_type_unsupported"
        assert q2["refusal"]["overridable"] is True


def test_import_refuses_a_file_this_build_cannot_read_unless_acknowledged(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    monkeypatch.setattr(models_router, "GPU_BACKEND", "amd")
    monkeypatch.setattr(models_router, "_call_agent_model", lambda path, payload, **_kwargs: {"status": "started"})
    state["details"] = _two_quantizations()

    refused = test_client.post("/api/models/huggingface/import", headers=test_client.auth_headers,
                               json={"repoId": REPO, "artifactId": "c" * 20})

    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "runtime_tensor_type_unsupported"
    assert not (models_router.Path(models_router.DATA_DIR) / "model-imports.json").exists()

    accepted = test_client.post("/api/models/huggingface/import", headers=test_client.auth_headers,
                                json={"repoId": REPO, "artifactId": "c" * 20, "allowUnsupportedRuntime": True})

    assert accepted.status_code == 200
    record = json.loads((models_router.Path(models_router.DATA_DIR) / "model-imports.json").read_text(encoding="utf-8"))["models"][0]
    assert record["runtime_override"]["tensorTypes"] == ["Q2_0"]
    assert record["runtime_override"]["build"] == "b9014"


def _sibling(filename, size=100, sha="a" * 64):
    return {"rfilename": filename, "size": size, "lfs": {"size": size, "sha256": sha}}


@pytest.mark.parametrize("names, default", [
    (["mmproj-BF16.gguf", "mmproj-F16.gguf", "mmproj-F32.gguf"], "mmproj-F16.gguf"),
    (["mmproj-BF16.gguf", "mmproj-F32.gguf"], "mmproj-BF16.gguf"),
    (["mmproj-model.gguf"], "mmproj-model.gguf"),          # the only one
    (["mmproj-a.gguf", "mmproj-b.gguf"], None),            # nothing to prefer: no guess
    ([], None),
])
def test_projectors_are_listed_and_the_default_is_llama_cpps_closest_pick(names, default):
    import routers.models as models_router

    payload = {"siblings": [_sibling("model-Q4_K_M.gguf")] + [_sibling(name) for name in names]}
    projectors = models_router._hf_gguf_projectors(payload)
    chosen = models_router._hf_default_projector(projectors)

    assert sorted(item["label"] for item in projectors) == sorted(names)
    assert (chosen or {}).get("label") == default
    # Projectors are never offered as weights.
    assert [artifact["label"] for artifact in models_router._hf_gguf_artifacts(payload)] == ["model-Q4_K_M.gguf"]


_PROJECTOR = {"id": "p" * 20, "label": "mmproj-F16.gguf", "filename": "mmproj-F16.gguf",
              "sizeBytes": 1 * GIB, "sha256": "9" * 64, "precision": "F16"}


def _vision_details():
    return _details(projectors=[_PROJECTOR], defaultProjectorId=_PROJECTOR["id"])


@pytest.mark.parametrize("extra, expect_projector", [({}, True), ({"includeVision": False}, False)])
def test_import_brings_the_projector_unless_vision_is_unticked(test_client, preflight_env, monkeypatch,
                                                              extra, expect_projector):
    models_router, state = preflight_env
    state["details"] = _vision_details()
    sent: list[dict] = []

    def agent(path, payload, **_kwargs):
        sent.append(payload)
        return {"status": "started"}

    monkeypatch.setattr(models_router, "_call_agent_model", agent)

    response = _import(test_client, **extra)

    assert response.status_code == 200
    record = json.loads((models_router.Path(models_router.DATA_DIR) / "model-imports.json").read_text(encoding="utf-8"))["models"][0]
    if expect_projector:
        assert record["mmproj_file"].startswith("hf-org-model-GGUF-mmproj-F16-") and record["mmproj_file"].endswith(".gguf")
        assert record["mmproj_sha256"] == "9" * 64 and record["mmproj_size_bytes"] == GIB
        assert record["mmproj_url"].endswith("/resolve/" + "c" * 40 + "/mmproj-F16.gguf")
        assert sent[0]["mmproj"] == {"file": record["mmproj_file"], "url": record["mmproj_url"], "sha256": "9" * 64}
    else:
        assert "mmproj_file" not in record and "mmproj" not in sent[0]


def test_an_unknown_projector_choice_is_refused(test_client, preflight_env, monkeypatch):
    models_router, state = preflight_env
    state["details"] = _vision_details()
    monkeypatch.setattr(models_router, "_call_agent_model", lambda *_a, **_k: pytest.fail("not dispatched"))

    response = _import(test_client, projectorId="q" * 20)

    assert response.status_code == 409


@pytest.mark.parametrize("artifact_id", [None, "b" * 20])
def test_preflight_counts_the_projector_in_fit_and_disk(test_client, preflight_env, artifact_id):
    _router, state = preflight_env
    state["details"] = _vision_details()
    # 20 GiB free, 5 GiB margin: the 4 GiB weights fit alone, 15 GiB weights + 1 GiB projector do not.
    state["details"]["artifacts"][1]["sizeBytes"] = 15 * GIB
    state["details"]["artifacts"][1]["files"][0]["sizeBytes"] = 15 * GIB

    url = f"/api/models/huggingface/preflight/{REPO}"
    if artifact_id:
        url += f"?artifactId={artifact_id}"
    body = test_client.get(url, headers=test_client.auth_headers).json()

    assert body["projector"] == {"id": "p" * 20, "label": "mmproj-F16.gguf", "sizeBytes": GIB, "precision": "F16"}
    assert body["artifacts"]["a" * 20]["disk"] == "ok"
    assert body["artifacts"]["b" * 20]["disk"] == "insufficient"


def test_selected_header_tensor_refusal_is_preserved_through_import(test_client, preflight_env, monkeypatch):
    router, state = preflight_env
    monkeypatch.setattr(router, "GPU_BACKEND", "amd")
    selected = {**_dense_header("qwen3"), "tensor_types": [42]}
    state["headers"] = {"model-Q8_0.gguf": selected}
    state["cached_headers"] = {"model-Q8_0.gguf": selected}
    monkeypatch.setattr(router, "_call_agent_model", lambda *_a, **_k: {"status": "started"})

    body = test_client.get(f"/api/models/huggingface/preflight/{REPO}?artifactId={'b' * 20}",
                          headers=test_client.auth_headers).json()
    assert body["artifacts"]["a" * 20]["tensors"]["status"] == "ok"
    check = body["artifacts"]["b" * 20]["tensors"]
    assert check["source"] == "header" and check["unknown"] == ["Q2_0"]
    assert check["refusal"]["code"] == "runtime_tensor_type_unsupported"
    state["reads"].clear()

    response = _import(test_client, artifactId="b" * 20, revision="c" * 40)
    assert response.status_code == 422
    assert not (router.Path(router.DATA_DIR) / "model-imports.json").exists()
    response = _import(test_client, artifactId="b" * 20, revision="c" * 40, allowUnsupportedRuntime=True)
    assert response.status_code == 200 and state["reads"] == []
    record = json.loads((router.Path(router.DATA_DIR) / "model-imports.json").read_text())["models"][0]
    assert record["architecture"] == "qwen3"
    assert record["runtime_override"]["tensorTypes"] == ["Q2_0"]


def test_a_windows_launcher_without_vision_support_imports_the_weights_alone(test_client, monkeypatch):
    import routers.models as models_router

    async def hub(path, **_kwargs):
        return {"id": REPO, "sha": "c" * 40, "siblings": [_sibling("model-Q4_K_M.gguf"), _sibling("mmproj-F16.gguf")]}, {}

    monkeypatch.setattr(models_router, "_hf_get_json", hub)
    monkeypatch.setattr(models_router, "_windows_hosted_runtime", lambda: True)
    management = {"managed": True, "canActivate": True, "canUnload": True, "running": True, "vision": False}
    monkeypatch.setattr(models_router, "_model_management_proof", lambda: dict(management))

    details = test_client.get(f"/api/models/huggingface/repositories/{REPO}", headers=test_client.auth_headers).json()

    assert [item["label"] for item in details["projectors"]] == ["mmproj-F16.gguf"]
    assert details["defaultProjectorId"] is None
    assert "Run Windows setup again" in details["visionUnavailableReason"]
    assert models_router._hf_requested_projector(details, {"includeVision": True}) is None

    management["vision"] = True
    details = test_client.get(f"/api/models/huggingface/repositories/{REPO}", headers=test_client.auth_headers).json()
    assert details["defaultProjectorId"] is not None
    assert details["visionUnavailableReason"] is None

    management.update(managed=None, vision=False)
    details = test_client.get(f"/api/models/huggingface/repositories/{REPO}", headers=test_client.auth_headers).json()
    assert details["defaultProjectorId"] is None
    assert "could not confirm" in details["visionUnavailableReason"]


def test_vision_support_is_read_only_from_a_managed_proof_and_never_projected(monkeypatch):
    import routers.models as models_router

    monkeypatch.setattr(models_router, "_windows_hosted_runtime", lambda: True)
    for value, expected in (({"managed": True, "canActivate": True, "canUnload": True, "running": True, "vision": True}, True),
                            ({"managed": True, "canActivate": True, "canUnload": True, "running": True}, False),
                            ({"managed": True, "canActivate": True, "canUnload": True, "running": True, "vision": "yes"}, False),
                            ({"managed": False, "canActivate": False, "canUnload": False, "running": False, "vision": True}, False)):
        monkeypatch.setattr(models_router, "request_agent_json", lambda *_args, value=value, **_kwargs: dict(value))
        assert models_router._model_management_proof()["vision"] is expected
        assert "vision" not in models_router._model_management()


def test_the_docker_desktop_windows_runtime_imports_the_weights_alone(monkeypatch):
    import routers.models as models_router

    monkeypatch.setattr(models_router, "read_live_env_values",
                        lambda keys: {"AMD_INFERENCE_RUNTIME_MODE": "windows-native-llama-server"})
    monkeypatch.setattr(models_router, "_model_management_proof", lambda: pytest.fail("no management proof is needed"))
    assert "does not load vision files" in models_router._projector_unavailable_reason()
    monkeypatch.setattr(models_router, "read_live_env_values", lambda keys: {"AMD_INFERENCE_RUNTIME_MODE": "linux-container"})
    monkeypatch.setattr(models_router, "_windows_hosted_runtime", lambda: False)
    assert models_router._projector_unavailable_reason() is None


def test_delete_waits_out_a_short_hold_and_refuses_a_long_one_in_words(test_client, monkeypatch):
    import routers.models as models_router

    monkeypatch.setattr(models_router, "_find_model_in_library", lambda model_id: {"id": model_id, "gguf_file": "x.gguf"})
    monkeypatch.setattr(models_router.time, "sleep", lambda _seconds: None)
    calls = []

    def agent(path, payload, **_kwargs):
        calls.append((path, payload["gguf_file"]))
        if len(calls) == 1:
            raise _busy("pixel_access_mode")  # the periodic Pixel re-proof
        return {"status": "deleted"}

    monkeypatch.setattr(models_router, "_call_agent_model", agent)
    response = test_client.delete("/api/models/some-import", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert calls == [("/v1/model/delete", "x.gguf")] * 2

    calls.clear()

    def switching(path, payload, **_kwargs):
        calls.append((path, payload["gguf_file"]))
        raise _busy("model_activation")  # not a short hold

    monkeypatch.setattr(models_router, "_call_agent_model", switching)
    response = test_client.delete("/api/models/some-import", headers=test_client.auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["message"] == (
        "ODS is switching models right now, so this model cannot be deleted yet. Try again in a minute.")
    assert calls == [("/v1/model/delete", "x.gguf")]
