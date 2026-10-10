"""Pre-download facts about a Hugging Face GGUF (pure functions)."""

import json

import pytest

import model_preflight as preflight
from model_memory import estimate_model_memory

POLICY = {
    "backendBuilds": {"nvidia": "b11429", "cpu": "b9014", "amd": "b9014",
                      "apple": "b9014", "windows-native": "b9014"},
    "builds": {
        "b9014": {"architectures": ["gemma2", "llama", "qwen3", "qwen35"]},
        "b11429": {"architectures": ["gemma2", "llama", "nanbeige", "qwen3", "qwen35"]},
    },
}


def _header(architecture="qwen3", template="{% if tools %}<tool_call>{% endif %}", **extra):
    metadata = {"general.architecture": architecture}
    if template is not None:
        metadata["tokenizer.chat_template"] = template
    metadata.update(extra.pop("metadata", {}))
    header = {"architecture": architecture, "metadata": metadata}
    header.update(extra)
    return header


# --- runtime policy ---------------------------------------------------------

def test_load_runtime_policy_rejects_missing_or_malformed(tmp_path):
    assert preflight.load_runtime_policy(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert preflight.load_runtime_policy(bad) is None
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"builds": {}}), encoding="utf-8")
    assert preflight.load_runtime_policy(wrong) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps(POLICY), encoding="utf-8")
    assert preflight.load_runtime_policy(good) == POLICY


@pytest.mark.parametrize("backend,windows_hosted,key,build", [
    ("nvidia", False, "nvidia", "b11429"),
    ("NVIDIA", False, "nvidia", "b11429"),
    ("amd", False, "amd", "b9014"),
    ("apple", False, "apple", "b9014"),
    ("", False, "cpu", "b9014"),
    ("none", False, "cpu", "b9014"),
    ("amd", True, "windows-native", "b9014"),
    ("nvidia", True, "windows-native", "b9014"),
    ("jetson", False, "jetson", None),
])
def test_effective_build_follows_the_backend_policy(backend, windows_hosted, key, build):
    assert preflight.effective_build(POLICY, backend, windows_hosted=windows_hosted) == (key, build)


def test_a_model_image_names_its_own_build():
    image = "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014@sha256:" + "f" * 64
    assert preflight.effective_build(POLICY, "nvidia", windows_hosted=False, model_image=image) == ("nvidia", "b9014")


def test_effective_build_without_policy_is_unknown():
    assert preflight.effective_build(None, "nvidia", windows_hosted=False) == ("nvidia", None)


@pytest.mark.parametrize("build,architecture,expected", [
    ("b11429", "nanbeige", True),
    ("b9014", "nanbeige", False),
    ("b9014", "qwen35", True),
    ("b9014", "", None),
    (None, "qwen3", None),
    ("b1", "qwen3", None),
])
def test_architecture_support(build, architecture, expected):
    assert preflight.architecture_supported(POLICY, build, architecture) is expected


# --- model kind -------------------------------------------------------------

@pytest.mark.parametrize("header,pipeline,tags,kind", [
    (_header("clip", metadata={"clip.has_vision_encoder": True}), "", [], "projector"),
    (_header("qwen3", metadata={"tokenizer.chat_template.rerank": "{{ query }}"}), "", [], "reranker"),
    (_header("qwen3", metadata={"qwen3.classifier.output_labels": ["yes", "no"]}), "", [], "reranker"),
    (_header("qwen3", metadata={"qwen3.pooling_type": 4}), "", [], "reranker"),
    (_header("nomic-bert", metadata={"nomic-bert.pooling_type": 1}), "", [], "embedding"),
    (_header("gemma3", metadata={"gemma3.attention.causal": False}), "", [], "embedding"),
    (_header("qwen3tts"), "", [], "speech"),
    (_header("qwen3"), "text-generation", [], "chat"),
    (None, "text-ranking", [], "reranker"),
    (None, "", ["text-ranking"], "reranker"),
    (None, "sentence-similarity", [], "embedding"),
    (None, "", ["sentence-transformers"], "embedding"),
    (None, "automatic-speech-recognition", [], "speech"),
    (None, "text-to-image", [], "image"),
    (None, "", [], "chat"),
])
def test_model_kind(header, pipeline, tags, kind):
    assert preflight.model_kind(header, pipeline, tags) == kind


def test_header_overrides_a_chat_pipeline_tag():
    # The live Qwen3-Reranker GGUF is tagged conversational; its header says rerank.
    header = _header("qwen3", metadata={"qwen3.pooling_type": 4})
    assert preflight.model_kind(header, "text-generation", ["conversational"]) == "reranker"


# --- template signals -------------------------------------------------------

@pytest.mark.parametrize("template,tools,thinking", [
    ("{% if tools %}<tool_call>{% endif %}{% if enable_thinking %}<think>{% endif %}", True, "toggle"),
    ("<|start|>{{ reasoning_effort }}<|channel|>analysis", False, "effort"),
    ("{{ messages }}<think>\n", False, "tags"),
    ("{% for m in messages %}{{ m.content }}{% endfor %}", False, "none"),
    ("{% for call in message.tool_calls %}{% endfor %}", True, "none"),
])
def test_template_signals(template, tools, thinking):
    signals = preflight.template_signals(_header(template=template))
    assert signals == {"present": True, "tools": tools, "thinking": thinking}


def test_template_signals_without_template_or_header():
    assert preflight.template_signals(_header(template=None)) == {
        "present": False, "tools": False, "thinking": "none"}
    assert preflight.template_signals(None) == {
        "present": None, "tools": None, "thinking": "unknown"}


# --- memory layout ----------------------------------------------------------

def _dense_header():
    return {
        "architecture": "llama", "context_length": 131072, "block_count": 32,
        "embedding_length": 4096, "attention_head_count": 32, "attention_head_count_kv": 8,
        "attention_key_length": 128, "attention_value_length": 128,
        "full_attention_interval": None, "ssm_conv_kernel": None, "ssm_inner_size": None,
        "ssm_state_size": None, "ssm_group_count": None, "ssm_time_step_rank": None,
        "metadata": {"general.architecture": "llama"},
    }


def test_dense_layout_is_marked_reviewed_for_the_architecture_estimate():
    fields = preflight.memory_fields(_dense_header())

    assert fields["recurrent_state_bytes"] == 0
    assert fields["max_context_length"] == 131072
    model = {**fields, "size_bytes": 4 * 1024 ** 3, "context_length": 32768}
    estimate = estimate_model_memory(model, context_length=32768)
    assert estimate.method == "architecture"
    # 32 layers x 8 KV heads x (128 + 128) x 2 bytes = 128 KiB per token.
    assert estimate.kv_bytes_per_token == 32 * 8 * (128 + 128) * 2


@pytest.mark.parametrize("change", [
    {"full_attention_interval": 4},
    {"ssm_state_size": 128},
    {"attention_head_count_kv": [8] * 32},
    {"metadata": {"llama.attention.sliding_window": 4096}},
    {"metadata": {"llama.attention.sliding_window_pattern": 6}},
])
def test_non_dense_layouts_keep_the_rough_estimate(change):
    header = _dense_header()
    header.update(change)

    fields = preflight.memory_fields(header)

    assert "recurrent_state_bytes" not in fields
    model = {**fields, "size_bytes": 4 * 1024 ** 3, "size_mb": 4096, "context_length": 32768}
    assert estimate_model_memory(model, context_length=32768).method == "legacy-heuristic"


def test_memory_fields_without_a_header():
    assert preflight.memory_fields(None) == {}


# --- disk and refusals -------------------------------------------------------

@pytest.mark.parametrize("needed,storage,status", [
    (10, {"freeBytes": 20, "marginBytes": 5}, "ok"),
    (10, {"freeBytes": 15, "marginBytes": 5}, "ok"),
    (10, {"freeBytes": 14, "marginBytes": 5}, "insufficient"),
    (10, None, "unknown"),
    (10, {"freeBytes": "x", "marginBytes": 5}, "unknown"),
])
def test_disk_status(needed, storage, status):
    assert preflight.disk_status(needed, storage) == status


def test_refusals_need_positive_evidence():
    assert preflight.refusal("chat", None, "qwen3", None) is None
    assert preflight.refusal("chat", True, "qwen3", "b9014") is None
    runtime = preflight.refusal("chat", False, "nanbeige", "b9014")
    assert runtime["code"] == "runtime_architecture_unsupported"
    assert runtime["overridable"] is True
    assert "b9014" in runtime["message"] and "nanbeige" in runtime["message"]
    kind = preflight.refusal("reranker", None, "qwen3", "b9014")
    assert kind == {
        "code": "not_a_chat_model:reranker",
        "message": preflight.KIND_MESSAGES["reranker"],
        "overridable": False,
    }


def test_a_gated_repository_is_refused_before_the_runtime_check():
    gated = preflight.refusal("chat", False, "nanbeige", "b9014", gated=True)
    assert gated == {"code": "gated", "message": preflight.GATED_MESSAGE, "overridable": False}
    # Not being a chat model outranks access: a token would not make an embedding model chat-capable.
    assert preflight.refusal("embedding", None, "nomic-bert", "b9014", gated=True)["code"] == "not_a_chat_model:embedding"
