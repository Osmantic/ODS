#!/usr/bin/env python3
"""vLLM entries in config/model-library.json follow model_vllm.py's schema.

A ``"runtime": "vllm"`` entry pins a Hugging Face snapshot (repo, full
commit, every file with its size and sha256 or git blob sha1) and the vLLM
arguments, GPU minimum and compute capabilities the installer needs. These
checks keep the shipped entries well formed and their numbers consistent:

* the schema itself (model_vllm.entry_problems), and that it catches the
  mistakes it is meant to catch;
* ``min_gpu_mib`` covers the weights plus 25% (the authoring rule) and so
  also model_memory's device need, weights / 0.90;
* every shard a safetensors set names is in the manifest;
* the two first entries keep the revisions and licences reviewed for them.

Network-free: verify a new manifest against the Hub's
``/api/models/<repo>/revision/<sha>?blobs=true`` before adding it.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"
sys.path.insert(0, str(ROOT / "extensions" / "services" / "dashboard-api"))

from model_memory import estimate_model_memory  # noqa: E402
from model_vllm import entry_problems, manifest_bytes, model_dir_name  # noqa: E402

MIB = 1024 ** 2
SHARD = re.compile(r"(?P<stem>.+)-(?P<index>[0-9]{5})-of-(?P<count>[0-9]{5})\.safetensors")
REVIEWED = {
    "qwen3.8-flash-next-nvfp4-vllm": {
        "hf_repo": "nvidia/Qwen3.8-Flash-Next-NVFP4",
        "revision": "fc694b54fb0174e0913e6adf86691ef85a4ead47",
        "license": "other", "license_name": "nvidia-open-model-license",
        "size_bytes": 132734506847, "files": 25,
    },
    "glm-5.3-flash-nvfp4-vllm": {
        "hf_repo": "nvidia/GLM-5.3-Flash-NVFP4",
        "revision": "da920bb0b9f4a06727223a349e55468e38352348",
        "license": "mit", "license_name": None,
        "size_bytes": 204476278199, "files": 44,
    },
}


def _models() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["models"]


def _vllm_models() -> list[dict]:
    return [model for model in _models() if model.get("runtime") == "vllm"]


def _ids(models):
    return [model["id"] for model in models]


def test_reviewed_entries_are_in_the_catalog():
    by_id = {model["id"]: model for model in _vllm_models()}
    assert set(REVIEWED) <= set(by_id)
    for model_id, expected in REVIEWED.items():
        model = by_id[model_id]
        assert model["hf_repo"] == expected["hf_repo"], model_id
        assert model["revision"] == expected["revision"], model_id
        assert model["license"] == expected["license"], model_id
        assert model.get("license_name") == expected["license_name"], model_id
        assert model["size_bytes"] == expected["size_bytes"], model_id
        assert len(model["files"]) == expected["files"], model_id
        assert model["compute_capability"] == ["10.0", "10.3"], model_id
        assert model["reasoning"] == "medium", model_id


@pytest.mark.parametrize("model", _vllm_models(), ids=_ids(_vllm_models()))
def test_entries_follow_the_schema(model):
    assert entry_problems(model) == []


@pytest.mark.parametrize("model", _vllm_models(), ids=_ids(_vllm_models()))
def test_min_gpu_memory_covers_the_weights_plus_a_quarter(model):
    weights_mib = manifest_bytes(model) / MIB
    assert model["min_gpu_mib"] >= weights_mib * 1.25, model["id"]
    # Rounded up to a whole GiB, not padded further.
    assert model["min_gpu_mib"] % 1024 == 0 and model["min_gpu_mib"] - weights_mib * 1.25 < 1024, model["id"]
    device_gib = estimate_model_memory(model).device_gib
    assert device_gib * 1024 <= model["min_gpu_mib"], (model["id"], device_gib)


@pytest.mark.parametrize("model", _vllm_models(), ids=_ids(_vllm_models()))
def test_every_safetensors_shard_is_in_the_manifest(model):
    paths = {item["path"] for item in model["files"]}
    shards: dict[tuple[str, str], set[int]] = {}
    for path in paths:
        match = SHARD.fullmatch(path)
        if match:
            shards.setdefault((match["stem"], match["count"]), set()).add(int(match["index"]))
    assert shards, model["id"]
    for (stem, count), indexes in shards.items():
        assert indexes == set(range(1, int(count) + 1)), (model["id"], stem)
    assert {"config.json", "model.safetensors.index.json", "tokenizer_config.json"} <= paths, model["id"]


def test_folders_served_names_and_model_names_are_unique():
    models = _vllm_models()
    every_name = [model.get("llm_model_name") for model in _models()]
    for key in (model_dir_name, lambda model: model["served_model_name"], lambda model: model["id"]):
        values = [key(model) for model in models]
        assert len(values) == len(set(values))
    for model in models:
        assert every_name.count(model["llm_model_name"]) == 1, model["id"]


def _valid() -> dict:
    return copy.deepcopy(next(model for model in _vllm_models() if model["id"] == "glm-5.3-flash-nvfp4-vllm"))


def _mutate(path: str, value):
    model = _valid()
    target = model
    *parents, last = path.split(".")
    for key in parents:
        target = target[int(key)] if isinstance(target, list) else target[key]
    if value is KeyError:
        del target[last]
    elif isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value
    return model


@pytest.mark.parametrize("path,value,expected", [
    ("gguf_file", "model.gguf", "llama.cpp fields do not apply"),
    ("revision", "da920bb0", "revision must be the full 40-hex commit"),
    ("hf_repo", "GLM-5.3-Flash-NVFP4", "hf_repo must be <owner>/<name>"),
    ("files.0.path", "../escape", "may not contain empty, '.' or '..' parts"),
    ("files.0.path", "/etc/passwd", "must be a relative POSIX path"),
    ("files.0.path", "README.md", "repeats README.md"),
    ("files.8.sha256", "ABC", "sha256 must be lowercase hex"),
    ("files.0.size", -1, "size must be a whole number of bytes"),
    ("files.0", {"path": "a.json", "size": 1}, "must be {path, size, sha256}"),
    ("size_bytes", 1, "size_bytes must be the manifest total"),
    ("context_length", 65536, "--max-model-len must equal the entry's context_length"),
    ("max_context_length", 65536, "max_context_length (config.json max_position_embeddings) must be at least"),
    ("tool_call_parser", "hermes", "--tool-call-parser must equal the entry's tool_call_parser"),
    ("vllm_args", ["--model", "/x"], "vllm_args may not set --model"),
    ("vllm_args", ["--speculative-config", '{"method": "mtp"}'], "vllm_args must be a list of plain strings"),
    ("min_gpu_mib", 0, "min_gpu_mib must be a positive whole number"),
    ("compute_capability", ["10"], 'compute_capability must list distinct "major.minor" strings'),
    ("reasoning", "on", "reasoning must be one of off, low, medium, high"),
    ("selection.discrete", 50, "selection.discrete must be 0"),
    ("selection.vllm", KeyError, "selection.vllm must be a whole number"),
    ("served_model_name", "GLM 5.3", "served_model_name must be letters, digits and ._-"),
])
def test_the_schema_catches_broken_entries(path, value, expected):
    problems = entry_problems(_mutate(path, value))
    assert any(expected in problem for problem in problems), problems


def _dotted_config(args: list[str], flag: str) -> dict:
    """The config vLLM builds from ``flag.key=value`` tokens: JSON values, else the text."""
    config = {}
    for arg in args:
        if arg.startswith(flag + "."):
            key, value = arg[len(flag) + 1:].split("=", 1)
            try:
                config[key] = json.loads(value)
            except json.JSONDecodeError:
                config[key] = value
    return config


# Measured on the DGX Station GB300 (vLLM v0.32.0, 10-27): MTP drafts lifted one-stream
# writing from about 200 to 313 tokens/s on Qwen and from 145 to 210 on GLM, and Qwen's
# n-gram embedding table on the GPU (not offloaded to the CPU) is part of that best setup.
MEASURED_FLAGS = {
    "qwen3.8-flash-next-nvfp4-vllm": {
        "--speculative-config": {"method": "mtp", "num_speculative_tokens": 3},
        "--engram-config": {"cpu_offload": False},
    },
    "glm-5.3-flash-nvfp4-vllm": {
        "--speculative-config": {"method": "mtp", "num_speculative_tokens": 5},
        "--engram-config": {},
    },
}


@pytest.mark.parametrize("model_id", sorted(MEASURED_FLAGS))
def test_the_entries_keep_the_flags_measured_on_a_dgx_station(model_id):
    model = {item["id"]: item for item in _vllm_models()}[model_id]
    for flag, expected in MEASURED_FLAGS[model_id].items():
        assert _dotted_config(model["vllm_args"], flag) == expected, (model_id, flag)


def test_the_broken_entry_cases_edit_what_they_name():
    # "repeats README.md" needs README.md after files[0]; "sha256 must be
    # lowercase hex" needs files[8] to be an LFS file.
    assert "README.md" in {item["path"] for item in _valid()["files"][1:]}
    assert "sha256" in _valid()["files"][8]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
