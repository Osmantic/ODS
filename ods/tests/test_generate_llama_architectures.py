"""scripts/generate-llama-architectures.py: parse LLM_ARCH_NAMES offline."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "generate_llama_architectures", ROOT / "scripts" / "generate-llama-architectures.py",
)
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)

SOURCE = """
#include "llama-arch.h"

static const std::map<llm_arch, const char *> LLM_ARCH_NAMES = {
    { LLM_ARCH_CLIP,             "clip"             }, // dummy, only used by llama-quantize
    { LLM_ARCH_LLAMA,            "llama"            },
    { LLM_ARCH_QWEN35,           "qwen35"           },
    { LLM_ARCH_GPT_OSS,          "gpt-oss"          },
    { LLM_ARCH_LLAMA,            "llama"            },
    { LLM_ARCH_UNKNOWN,          "(unknown)"        },
};

static const std::map<llm_kv, const char *> LLM_KV_NAMES = {
    { LLM_KV_GENERAL_TYPE, "general.type" },
};
"""


def test_names_are_sorted_unique_and_skip_unknown():
    assert generator.architecture_names(SOURCE) == ["clip", "gpt-oss", "llama", "qwen35"]


def test_only_the_architecture_map_is_read():
    names = generator.architecture_names(SOURCE)
    assert "general.type" not in names


@pytest.mark.parametrize("source,message", [
    ("int main() {}", "not found"),
    ("LLM_ARCH_NAMES = {\n    { LLM_ARCH_LLAMA, \"llama\" },", "not terminated"),
    ("LLM_ARCH_NAMES = {\n};", "no entries"),
])
def test_malformed_sources_fail_loudly(source, message):
    with pytest.raises(ValueError, match=message):
        generator.architecture_names(source)


def test_committed_document_has_a_list_for_every_policy_build():
    document = json.loads((ROOT / "config" / "llama-cpp-architectures.json").read_text(encoding="utf-8"))

    assert generator.render(document) == (ROOT / "config" / "llama-cpp-architectures.json").read_text(encoding="utf-8")
    assert set(document["builds"]) == set(document["backendBuilds"].values())
    for entry in document["builds"].values():
        assert entry["architectures"] == sorted(set(entry["architectures"]))


GGML_H = """
    // NOTE: always add types at the end of the enum to keep backward compatibility
    enum ggml_type {
        GGML_TYPE_F32     = 0,
        GGML_TYPE_F16     = 1,
        GGML_TYPE_Q4_0    = 2,
        // GGML_TYPE_Q4_2 = 4, support has been removed
        /* GGML_TYPE_Q4_3 = 5, support has been removed */
        GGML_TYPE_Q8_0    = 8,
        GGML_TYPE_Q4_K    = 12,
        GGML_TYPE_Q2_0    = 42,
        GGML_TYPE_COUNT   = 43,
    };
"""


def test_tensor_types_skip_removed_entries_and_the_count():
    assert generator.tensor_types(GGML_H) == {"F32": 0, "F16": 1, "Q4_0": 2, "Q8_0": 8, "Q4_K": 12, "Q2_0": 42}


def test_tensor_types_need_the_enum():
    with pytest.raises(ValueError, match="enum ggml_type"):
        generator.tensor_types("int main() {}")


def test_build_entry_reads_both_local_sources(tmp_path, monkeypatch):
    arch = tmp_path / "llama-arch.cpp"
    arch.write_text('LLM_ARCH_NAMES = {\n    { LLM_ARCH_LLAMA, "llama" },\n};\n', encoding="utf-8")
    ggml = tmp_path / "ggml.h"
    ggml.write_text(GGML_H, encoding="utf-8")
    monkeypatch.setattr(generator, "_fetch", lambda url: b'{"sha": "' + b"a" * 40 + b'"}')

    entry = generator.build_entry("b1", arch, ggml)

    assert entry == {"commit": "a" * 40, "architectures": ["llama"],
                     "tensorTypes": {"F32": 0, "F16": 1, "Q4_0": 2, "Q8_0": 8, "Q4_K": 12, "Q2_0": 42}}
