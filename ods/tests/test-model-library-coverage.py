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


def _agent_viable_for_release(model):
    compatibility = model.get("app_compatibility") or {}
    agent = compatibility.get("agent_viability") or {}
    agent_status = str(agent.get("status") or "").strip().lower()
    if agent_status in BLOCKING_AGENT_STATUSES:
        return False

    hermes = compatibility.get("hermes_talk") or {}
    hermes_status = str(hermes.get("status") or "").strip().lower()
    return hermes_status not in BLOCKING_AGENT_STATUSES


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
            assert int(artifact.get("size_bytes") or 0) > 0, model["id"]


def test_release_model_switchboard_catalog_ids_exist():
    expected = {
        "phi4-mini-q4",
        "phi3.5-mini-q4",
        "llama3.2-3b-instruct-q4",
        "qwen2.5-coder-3b-128k-q4",
        "qwen2.5-7b-instruct-q4",
        "llama3.1-8b-instruct-q4",
        "granite3.3-8b-instruct-q4",
        "mistral-nemo-12b-instruct-q4",
    }
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    ids = {model["id"] for model in catalog["models"]}

    assert expected <= ids


def test_llama32_3b_is_not_agent_viable_until_revalidated():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    compatibility = by_id["llama3.2-3b-instruct-q4"]["app_compatibility"]

    assert compatibility["agent_viability"]["status"] == "not_agent_viable"
    assert compatibility["agent_viability"]["evidence"]
    assert compatibility["hermes_talk"]["status"] == "unsupported_until_revalidated"
    assert not _agent_viable_for_release(by_id["llama3.2-3b-instruct-q4"])


def test_new_switchboard_models_do_not_change_install_recommendations():
    expected_switchboard_only = {
        "phi3.5-mini-q4",
        "llama3.2-3b-instruct-q4",
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
