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


def test_build_entry_reads_every_local_source(tmp_path, monkeypatch):
    arch = tmp_path / "llama-arch.cpp"
    arch.write_text('LLM_ARCH_NAMES = {\n    { LLM_ARCH_LLAMA, "llama" },\n};\n', encoding="utf-8")
    ggml = tmp_path / "ggml.h"
    ggml.write_text(GGML_H, encoding="utf-8")
    model = tmp_path / "llama-model.cpp"
    model.write_text(GENERIC_NEXTN, encoding="utf-8")
    monkeypatch.setattr(generator, "_fetch", lambda url: b'{"sha": "' + b"a" * 40 + b'"}')

    entry = generator.build_entry("b1", arch, ggml, model)

    assert entry == {"commit": "a" * 40, "architectures": ["llama"],
                     "tensorTypes": {"F32": 0, "F16": 1, "Q4_0": 2, "Q8_0": 8, "Q4_K": 12, "Q2_0": 42},
                     "nextnLoader": True, "nextnArchitectures": []}


GENERIC_NEXTN = """
void llama_model::load_hparams(llama_model_loader & ml) {
    ml.get_key(LLM_KV_NEXTN_PREDICT_LAYERS,    hparams.n_layer_nextn,   false);
}
"""
PER_ARCH_NEXTN = """
void llama_model::load_hparams(llama_model_loader & ml) {
    switch (arch) {
        case LLM_ARCH_GLM4_MOE:
            ml.get_key(LLM_KV_NEXTN_PREDICT_LAYERS, hparams.nextn_predict_layers, false);
            break;
    }
}
"""


def test_nextn_loader_needs_the_generic_layer_count():
    assert generator.nextn_loader(GENERIC_NEXTN) is True
    assert generator.nextn_loader(PER_ARCH_NEXTN) is False


def test_nextn_loader_rejects_an_unrelated_file():
    with pytest.raises(ValueError, match="load_hparams"):
        generator.nextn_loader("int main() {}")


def test_nextn_loader_ignores_comments_and_declarations():
    text = '''void llama_model::load_hparams() {
      // ml.get_key(LLM_KV_NEXTN_PREDICT_LAYERS, hparams.n_layer_nextn, false);
      hparams.n_layer_nextn = 0;
    }'''
    assert generator.nextn_loader(text) is False
    with pytest.raises(ValueError, match='coverage'):
        generator.nextn_architectures(text, '')


def test_legacy_nextn_capabilities_use_the_builds_actual_architecture_names():
    names = 'LLM_ARCH_NAMES = {\n {LLM_ARCH_GLM4_MOE, "glm4moe"},\n {LLM_ARCH_OTHER, "other-name"},\n};\n'
    assert generator.nextn_architectures(PER_ARCH_NEXTN, names) == ['glm4moe']
    shared = PER_ARCH_NEXTN.replace('case LLM_ARCH_GLM4_MOE:', 'case LLM_ARCH_OTHER: case LLM_ARCH_GLM4_MOE:')
    assert generator.nextn_architectures(shared, names) == ['glm4moe', 'other-name']
    with pytest.raises(ValueError, match='Unknown NextN'):
        generator.nextn_architectures(PER_ARCH_NEXTN, names.replace('LLM_ARCH_GLM4_MOE', 'LLM_ARCH_UNRELATED'))


def test_committed_document_records_nextn_loading_for_every_build():
    document = json.loads((ROOT / "config" / "llama-cpp-architectures.json").read_text(encoding="utf-8"))
    assert all(isinstance(entry["nextnLoader"], bool) for entry in document["builds"].values())


@pytest.mark.parametrize('outside', ['after-switch', 'default'])
def test_nextn_reader_outside_an_architecture_case_is_not_attributed(outside):
    read = 'ml.get_key(LLM_KV_NEXTN_PREDICT_LAYERS, hparams.nextn_predict_layers, false);'
    body = 'switch (arch) { case LLM_ARCH_LLAMA: break; '
    body += ('} ' + read) if outside == 'after-switch' else ('default: ' + read + ' }')
    model = 'void llama_model::load_hparams() {' + body + '}'
    assert generator.nextn_architectures(model, SOURCE) == []


def test_default_and_nested_switch_do_not_hide_later_architecture_cases():
    model = '''void llama_model::load_hparams() {
      switch (arch) {
        default: break;
        case LLM_ARCH_LLAMA: {
          switch (n) { case 0: break; default: break; }
          ml.get_key(LLM_KV_NEXTN_PREDICT_LAYERS, hparams.nextn_predict_layers, false);
          break;
        }
      }
    }'''
    assert generator.nextn_architectures(model, SOURCE) == ['llama']
