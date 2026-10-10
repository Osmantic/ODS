"""What ODS can tell about a Hugging Face GGUF before downloading it.

Pure functions over Hub metadata, the GGUF metadata header and this host's
llama.cpp release policy, so the Models page and the import endpoint reach
the same verdict. Everything here is advisory except two refusals made only
on positive evidence: the file is not a chat model, or this host's llama.cpp
build does not know its architecture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# llama.cpp llama_pooling_type: NONE=0, MEAN=1, CLS=2, LAST=3, RANK=4.
_EMBEDDING_POOLING = {1, 2, 3}
_RERANK_POOLING = 4
_EMBEDDING_PIPELINES = {"feature-extraction", "sentence-similarity"}
_RERANK_PIPELINES = {"text-ranking"}
_SPEECH_PIPELINES = {"automatic-speech-recognition", "audio-classification", "text-to-speech", "text-to-audio"}
_IMAGE_PIPELINES = {"text-to-image", "image-to-image", "image-classification", "zero-shot-image-classification"}
_SPEECH_ARCHITECTURES = {"wavtokenizer-dec"}

KIND_MESSAGES = {
    "embedding": "This is an embedding model. ODS runs embeddings in its RAG service, not as the chat model.",
    "reranker": "This is a reranking model. ODS runs reranking in its RAG service, not as the chat model.",
    "projector": "This file is a vision projector, not a model. It is used together with its model.",
    "speech": "This is a speech model. ODS runs speech in its voice services, not as the chat model.",
    "image": "This is an image model. ODS runs image generation in its image service, not as the chat model.",
}


def load_runtime_policy(path: Path) -> dict[str, Any] | None:
    """config/llama-cpp-architectures.json, or None when absent or malformed."""
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(policy, dict) or not isinstance(policy.get("backendBuilds"), dict) \
            or not isinstance(policy.get("builds"), dict):
        return None
    return policy


def runtime_policy_key(gpu_backend: Any, *, windows_hosted: bool) -> str:
    """The release-policy key of the llama.cpp runtime this host runs."""
    if windows_hosted:
        return "windows-native"
    backend = str(gpu_backend or "").strip().lower()
    if backend in {"", "none", "unknown"}:
        return "cpu"
    return backend


def build_from_image(image: Any) -> str | None:
    """b-number of a ghcr llama.cpp image tag such as server-cuda-b9014@sha256:..."""
    match = re.search(r"-(b\d+)(?:@|$)", str(image or "").strip())
    return match.group(1) if match else None


def effective_build(
    policy: dict[str, Any] | None,
    gpu_backend: Any,
    *,
    windows_hosted: bool,
    model_image: Any = None,
) -> tuple[str, str | None]:
    """(policy key, llama.cpp build) that would serve a model on this host."""
    key = runtime_policy_key(gpu_backend, windows_hosted=windows_hosted)
    image_build = build_from_image(model_image) if not windows_hosted else None
    if image_build:
        return key, image_build
    if not policy:
        return key, None
    build = policy["backendBuilds"].get(key)
    return key, build if isinstance(build, str) and build else None


def architecture_supported(policy: dict[str, Any] | None, build: str | None, architecture: Any) -> bool | None:
    """True/False when the build's architecture list is known, else None."""
    if not policy or not build or not isinstance(architecture, str) or not architecture.strip():
        return None
    entry = policy["builds"].get(build)
    names = entry.get("architectures") if isinstance(entry, dict) else None
    if not isinstance(names, list) or not names:
        return None
    return architecture.strip() in names


def _metadata(header: dict[str, Any] | None) -> dict[str, Any]:
    raw = (header or {}).get("metadata")
    return raw if isinstance(raw, dict) else {}


def _first_suffix(metadata: dict[str, Any], suffix: str) -> Any:
    for key, value in metadata.items():
        if key.endswith(suffix):
            return value
    return None


def model_kind(header: dict[str, Any] | None, pipeline_tag: Any, tags: Any) -> str:
    """chat, embedding, reranker, projector, speech or image.

    The GGUF header is authoritative; Hub pipeline tags are the fallback.
    """
    metadata = _metadata(header)
    architecture = str((header or {}).get("architecture") or metadata.get("general.architecture") or "")
    if architecture == "clip" or any(key.startswith("clip.") for key in metadata):
        return "projector"
    if "tokenizer.chat_template.rerank" in metadata \
            or _first_suffix(metadata, ".classifier.output_labels") is not None:
        return "reranker"
    pooling = _first_suffix(metadata, ".pooling_type")
    if isinstance(pooling, int) and not isinstance(pooling, bool):
        if pooling == _RERANK_POOLING:
            return "reranker"
        if pooling in _EMBEDDING_POOLING:
            return "embedding"
    if _first_suffix(metadata, ".attention.causal") is False:
        return "embedding"
    if architecture in _SPEECH_ARCHITECTURES or architecture.endswith("tts"):
        return "speech"
    pipeline = str(pipeline_tag or "").strip().lower()
    tag_set = {str(tag).lower() for tag in tags} if isinstance(tags, list) else set()
    if pipeline in _RERANK_PIPELINES or tag_set & _RERANK_PIPELINES:
        return "reranker"
    if pipeline in _EMBEDDING_PIPELINES or "sentence-transformers" in tag_set:
        return "embedding"
    if pipeline in _SPEECH_PIPELINES:
        return "speech"
    if pipeline in _IMAGE_PIPELINES:
        return "image"
    return "chat"


def template_signals(header: dict[str, Any] | None) -> dict[str, Any]:
    """What the file's own chat template says about tools and thinking.

    Advisory: llama.cpp derives the real tool-call and thinking format from
    the template at load time, and the first switch tests it.
    """
    template = _metadata(header).get("tokenizer.chat_template")
    if header is None:
        return {"present": None, "tools": None, "thinking": "unknown"}
    if not isinstance(template, str) or not template.strip():
        return {"present": False, "tools": False, "thinking": "none"}
    tools = any(marker in template for marker in ("tool_call", "tools"))
    if "enable_thinking" in template:
        thinking = "toggle"
    elif "reasoning_effort" in template or "<|channel|>" in template:
        thinking = "effort"
    elif "<think>" in template or "</think>" in template or "reasoning_content" in template:
        thinking = "tags"
    else:
        thinking = "none"
    return {"present": True, "tools": tools, "thinking": thinking}


def memory_fields(header: dict[str, Any] | None) -> dict[str, Any]:
    """model_memory inputs read from the GGUF header.

    ``recurrent_state_bytes`` is model_memory's "layout reviewed" marker. It
    is set (to 0) only for a plain dense transformer: one KV-head count for
    every layer and no recurrent, interleaved or sliding-window attention.
    Every other layout keeps the size-and-KV estimate.
    """
    if not header:
        return {}
    fields: dict[str, Any] = {}
    for key in ("block_count", "embedding_length", "attention_head_count",
                "attention_head_count_kv", "attention_key_length", "attention_value_length",
                "full_attention_interval"):
        value = header.get(key)
        if value is not None:
            fields[key] = value
    metadata = _metadata(header)
    context = header.get("context_length")
    if isinstance(context, int) and not isinstance(context, bool) and context >= 512:
        fields["max_context_length"] = context
    dense = (
        isinstance(header.get("attention_head_count_kv"), int)
        and not isinstance(header.get("attention_head_count_kv"), bool)
        and header.get("full_attention_interval") is None
        and not any(header.get(key) is not None for key in (
            "ssm_conv_kernel", "ssm_inner_size", "ssm_state_size", "ssm_group_count", "ssm_time_step_rank",
        ))
        and _first_suffix(metadata, ".attention.sliding_window") is None
        and _first_suffix(metadata, ".attention.sliding_window_pattern") is None
        and isinstance(header.get("block_count"), int)
    )
    if dense:
        fields["recurrent_state_bytes"] = 0
    return fields


def disk_status(needed_bytes: int, storage: dict[str, Any] | None) -> str:
    """ok, insufficient or unknown for one artifact against the model store."""
    if not storage:
        return "unknown"
    free = storage.get("freeBytes")
    margin = storage.get("marginBytes")
    if not isinstance(free, int) or not isinstance(margin, int):
        return "unknown"
    return "ok" if free >= needed_bytes + margin else "insufficient"


GATED_MESSAGE = (
    "This model is gated on Hugging Face. Sign in at huggingface.co and accept its license on the "
    "model page, then add your Hugging Face access token as HF_TOKEN in Settings (Open Environment "
    "Editor) and open this model again."
)


def refusal(
    kind: str,
    supported: bool | None,
    architecture: Any,
    build: str | None,
    *,
    gated: bool = False,
) -> dict[str, Any] | None:
    """The repository-level hard refusal, if any (positive evidence only).

    ``gated`` means the Hub will not serve this repository's files to this
    host: it is gated and either no token is set or the token was refused.
    """
    if kind != "chat":
        return {"code": f"not_a_chat_model:{kind}", "message": KIND_MESSAGES[kind], "overridable": False}
    if gated:
        return {"code": "gated", "message": GATED_MESSAGE, "overridable": False}
    if supported is False:
        return {
            "code": "runtime_architecture_unsupported",
            "message": (
                f"This model needs a newer llama.cpp than this machine runs (build {build}). "
                f"Its architecture, {architecture}, arrived in a later release. Choose another model, "
                "or import anyway if you know this runtime can load it."
            ),
            "overridable": True,
        }
    return None
