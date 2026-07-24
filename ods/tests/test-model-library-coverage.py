import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"
HERMES_CONTEXT_FLOOR = 65536


def _download_artifacts(model):
    parts = model.get("gguf_parts")
    if isinstance(parts, list) and parts:
        return parts
    return [{
        "file": model.get("gguf_file"),
        "url": model.get("gguf_url"),
        "sha256": model.get("gguf_sha256"),
        "size_bytes": model.get("size_bytes"),
        "size_mb": model.get("size_mb"),
    }]


BLOCKING_AGENT_STATUSES = {
    "blocked",
    "incompatible",
    "not_agent_viable",
    "not_recommended",
    "not_supported",
    "unsupported",
    "unsupported_until_revalidated",
}


def _has_runtime_scope(entry):
    return any(
        key in entry
        for key in {
            "gpuBackendScope",
            "gpu_backend_scope",
            "llmBackendScope",
            "llm_backend_scope",
            "runtimeScope",
            "runtime_scope",
            "odsModeScope",
            "ods_mode_scope",
        }
    )


def _agent_viable_for_release(model, host=None):
    if str(model.get("source") or "").strip().lower() not in {"", "curated"}:
        return False
    compatibility = model.get("app_compatibility") or {}
    for entry in compatibility.values():
        entry = entry or {}
        status = str((entry or {}).get("status") or "").strip().lower()
        if status not in BLOCKING_AGENT_STATUSES or _has_runtime_scope(entry):
            continue
        host_scope = entry.get("hostScope") or entry.get("host_scope")
        if host_scope:
            scoped_hosts = {str(value).strip().lower() for value in host_scope}
            if host is not None and str(host).strip().lower() in scoped_hosts:
                return False
            continue
        return False
    return True


def test_low_vram_catalog_has_six_agent_viable_downloadable_models():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    models = catalog["models"]
    low_vram = [
        model
        for model in models
        if int(model.get("vram_required_gb") or 0) <= 8
        and int(model.get("context_length") or 0) >= HERMES_CONTEXT_FLOOR
        and _agent_viable_for_release(model)
    ]

    assert len(low_vram) >= 6

    for model in low_vram:
        artifacts = _download_artifacts(model)
        assert artifacts, model["id"]
        for artifact in artifacts:
            assert artifact.get("file"), model["id"]
            assert str(artifact.get("url") or "").startswith("https://huggingface.co/"), model["id"]
            assert len(str(artifact.get("sha256") or "")) == 64, model["id"]
            assert int(artifact.get("size_bytes") or artifact.get("size_mb") or 0) > 0, model["id"]


def test_huggingface_import_is_not_agent_viable_for_release():
    assert not _agent_viable_for_release({"source": "huggingface"})


def test_release_model_switchboard_catalog_ids_exist():
    expected = {
        "phi4-mini-q4",
        "phi3.5-mini-q4",
        "qwen2.5-0.5b-instruct-q4",
        "qwen2.5-1.5b-instruct-q4",
        "granite3.3-2b-instruct-q4",
        "smollm3-3b-q4",
        "granite4.0-h-1b-q4",
        "falcon-h1-1.5b-instruct-q4",
        "falcon-h1-3b-instruct-q4",
        "granite4.0-1b-q4",
        "granite4.0-h-350m-q4",
        "granite3.2-2b-instruct-q4",
        "granite3.1-2b-instruct-q4",
        "phi3-mini-128k-q4",
        "llama3.2-1b-instruct-q4",
        "llama3.2-3b-instruct-q4",
        "qwen2.5-3b-instruct-q4",
        "qwen3-4b-q4",
        "qwen3-4b-instruct-2507-q4",
        "qwen3-4b-128k-q4",
        "qwen3-1.7b-q4",
        "qwen2.5-coder-1.5b-128k-q4",
        "qwen2.5-coder-3b-128k-q4",
        "qwen2.5-7b-instruct-q4",
        "llama3.1-8b-instruct-q4",
        "granite3.3-8b-instruct-q4",
        "mistral-nemo-12b-instruct-q4",
    }
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    ids = {model["id"] for model in catalog["models"]}

    assert expected <= ids


def test_llama32_1b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["llama3.2-1b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "cycle-004" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["llama3.2-1b-instruct-q4"])


def test_llama32_3b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["llama3.2-3b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["llama3.2-3b-instruct-q4"])


def test_phi4_mini_is_not_agent_viable_after_strixy_talk_probe_failure():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["phi4-mini-q4"]["app_compatibility"]

    assert compatibility["openai_chat"]["status"] == "verified"
    assert "42b3a95c" in compatibility["openai_chat"]["reason"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "strixy" in compatibility["agent_viability"]["evidence"]
    assert "cycle-001" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert "strixy" in compatibility["hermes_talk"]["evidence"]
    assert not _agent_viable_for_release(by_id["phi4-mini-q4"])


def test_phi3_mini_128k_requires_perplexica_revalidation_after_strixy_failure():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["phi3-mini-128k-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "tower2" in compatibility["agent_viability"]["hostScope"]
    assert "cycle-006" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert "generic assistant greeting" in compatibility["hermes_talk"]["reason"]
    assert compatibility["perplexica"]["status"] == "unsupported_until_revalidated"
    assert "hostScope" not in compatibility["perplexica"]
    assert "strixy" in compatibility["perplexica"]["reason"].lower()
    assert "apology prose" in compatibility["perplexica"]["reason"]
    assert "cycle-006/strixy" in compatibility["perplexica"]["evidence"]
    assert not _agent_viable_for_release(by_id["phi3-mini-128k-q4"])


def test_llama31_8b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["llama3.1-8b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert compatibility["hermes_talk"]["evidence"]
    assert not _agent_viable_for_release(by_id["llama3.1-8b-instruct-q4"])


def test_phi35_mini_requires_perplexica_revalidation_globally_and_runtime_revalidation_on_windows():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["phi3.5-mini-q4"]["app_compatibility"]

    assert compatibility["openai_chat"]["status"] == "unsupported_until_revalidated"
    assert compatibility["openai_chat"]["evidence"]
    assert compatibility["perplexica"]["status"] == "unsupported_until_revalidated"
    assert compatibility["perplexica"]["globalScope"] is True
    assert "tower2" in compatibility["perplexica"]["reason"]
    assert "strix-halo" in compatibility["perplexica"]["reason"]
    assert "cycle-001/{tower2,strix-halo}" in compatibility["perplexica"]["evidence"]
    assert not _agent_viable_for_release(by_id["phi3.5-mini-q4"])
    assert not _agent_viable_for_release(by_id["phi3.5-mini-q4"], host="windows-laptop")


def test_qwen25_15b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["qwen2.5-1.5b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["qwen2.5-1.5b-instruct-q4"])


def test_qwen25_05b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["qwen2.5-0.5b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "cycle-003" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["qwen2.5-0.5b-instruct-q4"])


def test_qwen25_3b_runtime_context_conflict_blocks_agent_coverage():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["qwen2.5-3b-instruct-q4"]
    compatibility = model["app_compatibility"]

    assert model["vram_required_gb"] <= 4
    assert model["context_length"] == 32768
    assert compatibility["openai_chat"]["status"] == "verified"
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "32,768" in compatibility["agent_viability"]["reason"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(model)


def test_granite33_2b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["granite3.3-2b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "windows-laptop" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["granite3.3-2b-instruct-q4"])


def test_smollm3_3b_runtime_context_is_64k_and_it_remains_a_revalidation_candidate():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    model = by_id["smollm3-3b-q4"]
    compatibility = model["app_compatibility"]

    assert model["context_length"] == 65536
    assert compatibility["openai_chat"]["status"] == "verified"
    assert "58944ba461fb" in compatibility["openai_chat"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "verified"
    assert compatibility["hermes_talk"]["productSha"] == "58944ba461fb87b87b1ce6fa854e32d15aeb8efa"
    assert compatibility["perplexica"]["status"] == "unsupported_until_revalidated"
    assert "llmBackendScope" not in compatibility["perplexica"]
    assert "hostScope" not in compatibility["perplexica"]
    assert "cycle-003/tower2" in compatibility["perplexica"]["evidence"]
    assert "cycle-003/strix-halo" in compatibility["perplexica"]["evidence"]
    assert "cycle-003/spark" in compatibility["perplexica"]["evidence"]
    assert "cycle-003/m5-mbp" in compatibility["perplexica"]["evidence"]
    assert "cycle-003/windows-laptop" in compatibility["perplexica"]["evidence"]
    assert "cycle-003/strixy" in compatibility["perplexica"]["evidence"]
    assert "agent_viability" not in compatibility
    assert not _agent_viable_for_release(model)


def test_qwen3_4b_128k_talk_block_is_global_after_linux_revalidation():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    model = by_id["qwen3-4b-128k-q4"]
    compatibility = model["app_compatibility"]["hermes_talk"]

    assert compatibility["status"] == "unsupported_until_revalidated"
    assert compatibility["globalScope"] is True
    assert "hostScope" not in compatibility
    assert "cycle-006/m5-mbp" in compatibility["evidence"]
    assert "cycle-005/windows-laptop" in compatibility["evidence"]
    assert "cycle-006/tower2" in compatibility["evidence"]
    assert ".MEDIA" in compatibility["reason"]
    assert "180-second" in compatibility["reason"]
    assert "3.9 seconds" in compatibility["reason"]
    assert not _agent_viable_for_release(model)
    assert not _agent_viable_for_release(model, host="m5-mbp")
    assert not _agent_viable_for_release(model, host="windows-laptop")


def test_windows_8gb_revalidation_models_have_64k_compressed_kv_profiles():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    expected = {
        "qwen3-4b-instruct-2507-q4": ("nvidia-8gb-64k-q4-kv", "q4_0", 7.2),
        "qwen3.5-4b-q4": ("nvidia-8gb-64k-q4-kv", "q4_0", 7.2),
    }
    for model_id, (profile_id, cache_type, required_gb) in expected.items():
        model = by_id[model_id]
        profiles = {profile["id"]: profile for profile in model["runtime_profiles"]}
        profile = profiles[profile_id]

        assert profile["backend"] == "nvidia"
        assert profile["host_arch"] == ["amd64"]
        assert profile["memory_type"] == "discrete"
        assert profile["vram_min_gb"] == 7.5
        assert profile["vram_max_gb"] == 8.5
        assert profile["system_ram_min_gb"] == 31
        assert profile["context_length"] == HERMES_CONTEXT_FLOOR
        assert profile["estimated_required_gb"] == required_gb
        assert profile["env"]["LLAMA_PARALLEL"] == "1"
        assert profile["env"]["LLAMA_ARG_FLASH_ATTN"] == "on"
        assert profile["env"]["LLAMA_ARG_CACHE_TYPE_K"] == cache_type
        assert profile["env"]["LLAMA_ARG_CACHE_TYPE_V"] == cache_type


def test_windows_8gb_revalidation_models_have_verified_app_evidence():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    expected_cycles = {
        "qwen3-4b-instruct-2507-q4": "cycle-001/windows-laptop",
        "qwen3.5-4b-q4": "cycle-002/windows-laptop",
    }

    for model_id, cycle_path in expected_cycles.items():
        compatibility = by_id[model_id]["app_compatibility"]
        for app in ("openai_chat", "hermes_talk", "perplexica", "agent_viability"):
            verdict = compatibility[app]
            assert verdict["status"] == "verified"
            assert verdict["hostScope"] == ["windows-laptop"]
            assert verdict["productSha"] == "449cf84d866d8bdedd8046d3c58faab6c07b5f03"
            assert verdict["harnessSha"] == "954deb755b0730719512ac3675a748474180e01c"
            assert cycle_path in verdict["evidence"]


def test_granite31_requires_global_perplexica_revalidation_after_strixy_failure():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["granite3.1-2b-instruct-q4"]
    compatibility = model["app_compatibility"]["perplexica"]

    assert compatibility["status"] == "unsupported_until_revalidated"
    assert "hostScope" not in compatibility
    assert "tower2" in compatibility["reason"]
    assert "strixy" in compatibility["reason"]
    assert "cycle-006/strixy" in compatibility["evidence"]
    assert not _agent_viable_for_release(model)


def test_granite4_h_1b_requires_perplexica_revalidation_after_m5_partial_reply():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["granite4.0-h-1b-q4"]
    compatibility = model["app_compatibility"]
    assert model["vram_required_gb"] <= 3
    assert model["context_length"] >= HERMES_CONTEXT_FLOOR
    assert model["gguf_sha256"] == "da3d737121a96f3c9a316685212376257a7f167b74380855666dd488d6af3bcb"
    assert model["gguf_url"].startswith("https://huggingface.co/ibm-granite/granite-4.0-h-1b-GGUF/")
    assert compatibility["perplexica"]["status"] == "unsupported_until_revalidated"
    assert "m5-mbp" in compatibility["perplexica"]["reason"]
    assert "Perplexica" in compatibility["perplexica"]["reason"]
    assert "cycle-003" in compatibility["perplexica"]["evidence"]
    assert _agent_viable_for_release(model)
    assert not _agent_viable_for_release(model, host="m5-mbp")


def test_falcon_h1_15b_is_not_low_vram_agent_viable_after_opencode_failure():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["falcon-h1-1.5b-instruct-q4"]
    compatibility = model["app_compatibility"]
    assert model["vram_required_gb"] <= 3
    assert model["context_length"] >= HERMES_CONTEXT_FLOOR
    assert model["gguf_sha256"] == "8b51aa2aa34a0373fd0cd64c02eb91d1bc1da681c09e955ad769d4a9b2d8385f"
    assert model["gguf_url"].startswith("https://huggingface.co/tiiuae/Falcon-H1-1.5B-Instruct-GGUF/")
    assert model["size_bytes"] == 944786656
    assert compatibility["opencode"]["status"] == "unsupported_until_revalidated"
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert not _agent_viable_for_release(model)


def test_granite32_2b_is_direct_chat_only_after_windows_talk_timeout():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["granite3.2-2b-instruct-q4"]
    compatibility = model["app_compatibility"]

    assert compatibility["openai_chat"]["status"] == "verified"
    assert "0.73 tok/s" in compatibility["openai_chat"]["reason"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "19,349-token Hermes prompt" in compatibility["agent_viability"]["reason"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert "cycle-004" in compatibility["hermes_talk"]["evidence"]
    assert _agent_viable_for_release(model)
    assert not _agent_viable_for_release(model, host="windows-laptop")


def test_granite4_h_350m_is_not_agent_viable_after_talk_probe_failure():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["granite4.0-h-350m-q4"]
    compatibility = model["app_compatibility"]

    assert model["vram_required_gb"] <= 3
    assert model["context_length"] >= HERMES_CONTEXT_FLOOR
    assert model["gguf_sha256"] == "0a8d6a7373602fadfba274a640ba784b86cc6847f1c67f1b0a90fa2ec266b7fb"
    assert model["gguf_url"].startswith("https://huggingface.co/ibm-granite/granite-4.0-h-350m-GGUF/")
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "cycle-005" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(model)


def test_replacement_low_vram_long_context_models_are_cataloged_for_validation():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    expected = {
        "qwen3-4b-instruct-2507-q4": (
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/",
            "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        ),
        "qwen3-4b-128k-q4": (
            "https://huggingface.co/unsloth/Qwen3-4B-128K-GGUF/",
            "f145a1bd60fec420ca4d9b7645ebcdf657e301463bc4dd3af4a8c0b548b5eb1a",
        ),
        "granite3.1-2b-instruct-q4": (
            "https://huggingface.co/bartowski/granite-3.1-2b-instruct-GGUF/",
            "774269c82fde2720ea18dcf457fb5bd028fe096139a0735f4ad59c0a270cfc9c",
        ),
        "phi3-mini-128k-q4": (
            "https://huggingface.co/QuantFactory/Phi-3-mini-128k-instruct-GGUF/",
            "3b27c1a245243b3eadf6db453ddefd419a31e388820824beeba1c60eee17d05e",
        ),
    }

    for model_id, (url_prefix, sha256) in expected.items():
        model = by_id[model_id]
        assert model["vram_required_gb"] <= 5
        assert model["context_length"] >= HERMES_CONTEXT_FLOOR
        assert model["gguf_sha256"] == sha256
        assert model["gguf_url"].startswith(url_prefix)
        if model_id in {
            "granite3.1-2b-instruct-q4",
            "phi3-mini-128k-q4",
            "qwen3-4b-128k-q4",
        }:
            assert not _agent_viable_for_release(model)
        else:
            assert _agent_viable_for_release(model)


def test_granite33_8b_has_visible_nvidia_8gb_release_profile():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["granite3.3-8b-instruct-q4"]
    profiles = {profile["id"]: profile for profile in model["runtime_profiles"]}
    profile = profiles["nvidia-8gb-64k"]

    assert model["gguf_url"].startswith("https://huggingface.co/ibm-granite/granite-3.3-8b-instruct-GGUF/")
    assert model["gguf_sha256"] == "77bcee066a76dcdd10d0d123c87e32c8ec2c74e31b6ffd87ebee49c9ac215dca"
    assert model["size_bytes"] == 4942873344
    assert model["context_length"] == 128000
    assert profile["backend"] == "nvidia"
    assert profile["memory_type"] == "discrete"
    assert profile["vram_min_gb"] == 7.5
    assert profile["vram_max_gb"] == 8.5
    assert profile["context_length"] == HERMES_CONTEXT_FLOOR
    assert profile["estimated_required_gb"] < 8
    compatibility = model["app_compatibility"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "cycle-006" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(model)


def test_granite4_dense_1b_is_direct_chat_only_until_talk_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["granite4.0-1b-q4"]
    compatibility = model["app_compatibility"]

    assert model["vram_required_gb"] <= 3
    assert model["context_length"] >= HERMES_CONTEXT_FLOOR
    assert model["gguf_sha256"] == "22ec0f9cc99a90185312de3c882c84e7bd6789bdd050389844380a01a831d7f1"
    assert model["gguf_url"].startswith("https://huggingface.co/ibm-granite/granite-4.0-1b-GGUF/")
    assert compatibility["openai_chat"]["status"] == "verified"
    assert "0.93 tok/s" in compatibility["agent_viability"]["reason"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(model)


def test_qwen3_4b_is_blocked_after_windows_talk_timeout():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["qwen3-4b-q4"]
    compatibility = model["app_compatibility"]

    assert model["vram_required_gb"] <= 5
    assert model["context_length"] == 40960
    assert model["gguf_sha256"] == "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
    assert model["gguf_url"].startswith("https://huggingface.co/Qwen/Qwen3-4B-GGUF/")
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "0.5 tok/s" in compatibility["agent_viability"]["reason"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(model)


def test_qwen3_17b_is_below_release_context_floor_without_yarn_policy():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    model = by_id["qwen3-1.7b-q4"]
    compatibility = model["app_compatibility"]

    assert model["vram_required_gb"] <= 3
    assert model["context_length"] == 40960
    assert model["gguf_sha256"] == "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5"
    assert model["gguf_url"].startswith("https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/")
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "max_position_embeddings=40960" in compatibility["agent_viability"]["reason"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(model)


def test_qwen25_coder_3b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["qwen2.5-coder-3b-128k-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["qwen2.5-coder-3b-128k-q4"])


def test_falcon_h1_15b_is_not_talk_or_opencode_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["falcon-h1-1.5b-instruct-q4"]["app_compatibility"]

    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert "strixy" in compatibility["hermes_talk"]["reason"]
    assert "cycle-004" in compatibility["hermes_talk"]["evidence"]
    assert compatibility["opencode"]["status"] == "unsupported_until_revalidated"
    assert "OpenCode" in compatibility["opencode"]["reason"]
    assert "cycle-004" in compatibility["opencode"]["evidence"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "OpenCode" in compatibility["agent_viability"]["reason"]
    assert "ODS Talk" in compatibility["agent_viability"]["reason"]
    assert "hostScope" not in compatibility["agent_viability"]
    assert not _agent_viable_for_release(by_id["falcon-h1-1.5b-instruct-q4"])


def test_falcon_h1_3b_is_not_talk_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["falcon-h1-3b-instruct-q4"]["app_compatibility"]

    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert "random_uuid" in compatibility["hermes_talk"]["reason"]
    assert "cycle-004" in compatibility["hermes_talk"]["evidence"]
    assert "hostScope" not in compatibility["hermes_talk"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "tool-call payload" in compatibility["agent_viability"]["reason"]
    assert "hostScope" not in compatibility["agent_viability"]
    assert not _agent_viable_for_release(by_id["falcon-h1-3b-instruct-q4"])


def test_qwen25_coder_15b_128k_has_scoped_app_blocks_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    model = by_id["qwen2.5-coder-1.5b-128k-q4"]
    compatibility = model["app_compatibility"]

    assert model["gguf_sha256"] == "0fbff4d39395fab063c51377ba522928af2574b1f998d66012c1caed7b8f91d6"
    assert model["context_length"] >= HERMES_CONTEXT_FLOOR
    assert model["vram_required_gb"] <= 3
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert "generic assistant prose" in compatibility["hermes_talk"]["reason"]
    assert "cycle-006" in compatibility["hermes_talk"]["evidence"]
    assert compatibility["hermes_talk"]["hostScope"] == ["tower2"]
    assert compatibility["opencode"]["status"] == "unsupported_until_revalidated"
    assert "webfetch tool payload" in compatibility["opencode"]["reason"]
    assert compatibility["opencode"]["hostScope"] == ["strix-halo"]
    assert compatibility["perplexica"]["status"] == "unsupported_until_revalidated"
    assert "Eiffel Tower" in compatibility["perplexica"]["reason"]
    assert compatibility["perplexica"]["hostScope"] == ["strix-halo"]
    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["agent_viability"]["hostScope"] == ["tower2", "strix-halo"]
    assert _agent_viable_for_release(model)
    assert not _agent_viable_for_release(model, host="tower2")
    assert not _agent_viable_for_release(model, host="strix-halo")


def test_mistral_nemo_talk_block_is_scoped_to_apple_llama_server():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    model = by_id["mistral-nemo-12b-instruct-q4"]
    compatibility = model["app_compatibility"]

    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert compatibility["hermes_talk"]["gpuBackendScope"] == ["apple"]
    assert compatibility["hermes_talk"]["llmBackendScope"] == ["llama-server"]
    assert "m5-mbp" in compatibility["hermes_talk"]["hostScope"]
    assert "cycle-006" in compatibility["hermes_talk"]["evidence"]
    assert _agent_viable_for_release(model)


def test_qwen3_4b_long_context_replacements_are_release_candidates():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    expected = {
        "qwen3.5-4b-q4": {
            "sha": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
            "context": 262144,
            "size_bytes": 2740937888,
            "url": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/",
        },
        "qwen3-4b-instruct-2507-q4": {
            "sha": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
            "context": 262144,
            "url": "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/",
        },
        "qwen3-4b-128k-q4": {
            "sha": "f145a1bd60fec420ca4d9b7645ebcdf657e301463bc4dd3af4a8c0b548b5eb1a",
            "context": 131072,
            "url": "https://huggingface.co/unsloth/Qwen3-4B-128K-GGUF/",
        },
    }

    for model_id, expected_model in expected.items():
        model = by_id[model_id]
        assert model["gguf_sha256"] == expected_model["sha"]
        assert model["context_length"] == expected_model["context"]
        assert model["vram_required_gb"] <= 5
        assert model["gguf_url"].startswith(expected_model["url"])
        if "size_bytes" in expected_model:
            assert model["size_bytes"] == expected_model["size_bytes"]
        if model_id != "qwen3.5-4b-q4":
            assert model.get("install_recommendation") is False
        if model_id == "qwen3-4b-128k-q4":
            assert not _agent_viable_for_release(model)
        else:
            assert _agent_viable_for_release(model)


def test_qwen25_7b_is_not_agent_viable_on_low_vram_windows_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["qwen2.5-7b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert "windows-laptop" in compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["qwen2.5-7b-instruct-q4"])


def test_qwen35_9b_meets_hermes_context_floor():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    assert by_id["qwen3.5-9b-q4"]["context_length"] >= HERMES_CONTEXT_FLOOR


def test_new_switchboard_models_do_not_change_install_recommendations():
    expected_switchboard_only = {
        "phi3.5-mini-q4",
        "qwen2.5-0.5b-instruct-q4",
        "qwen2.5-1.5b-instruct-q4",
        "granite3.3-2b-instruct-q4",
        "smollm3-3b-q4",
        "granite4.0-h-1b-q4",
        "falcon-h1-1.5b-instruct-q4",
        "falcon-h1-3b-instruct-q4",
        "granite4.0-1b-q4",
        "granite4.0-h-350m-q4",
        "granite3.2-2b-instruct-q4",
        "granite3.1-2b-instruct-q4",
        "phi3-mini-128k-q4",
        "llama3.2-1b-instruct-q4",
        "llama3.2-3b-instruct-q4",
        "qwen2.5-3b-instruct-q4",
        "qwen3-4b-q4",
        "qwen3-4b-instruct-2507-q4",
        "qwen3-4b-128k-q4",
        "qwen3-1.7b-q4",
        "qwen2.5-coder-1.5b-128k-q4",
        "qwen2.5-coder-3b-128k-q4",
        "qwen2.5-7b-instruct-q4",
        "llama3.1-8b-instruct-q4",
        "granite3.3-8b-instruct-q4",
        "mistral-nemo-12b-instruct-q4",
    }
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}

    assert expected_switchboard_only <= set(by_id)
    for model_id in expected_switchboard_only:
        assert by_id[model_id].get("install_recommendation") is False, model_id
