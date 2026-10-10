"""Regression tests for fail-closed Cafe capability validation."""
import pytest

from cafe_llama_icd import validate_cafe_configuration


def configuration(**overrides):
    values = {
        "runtime": "cafe-llama.cpp",
        "kernel": "baseline",
        "kv_cache": "f16",
        "flash_attention": True,
        "offload": "none",
        "speculation": "none",
        "draft_tokens": 0,
        "context": 4096,
        "gpu_layers": 0,
    }
    values.update(overrides)
    return values


def capabilities(**overrides):
    values = {
        "kernel": ["baseline"],
        "kv_cache": ["f16", "q8_0"],
        "flash_attention": [True, False],
        "offload": ["none"],
        "speculation": ["none"],
    }
    values.update(overrides)
    return values


def test_flash_attention_must_be_advertised_by_probed_build():
    proposed = configuration(flash_attention=True)
    validate_cafe_configuration(proposed, runtime_capabilities=capabilities())

    with pytest.raises(ValueError, match="does not advertise flash_attention"):
        validate_cafe_configuration(
            proposed,
            runtime_capabilities=capabilities(flash_attention=[]),
        )


def test_turbo_kv_requires_explicit_flash_attention_true():
    validate_cafe_configuration(configuration(kv_cache="turbo4", flash_attention=True))

    with pytest.raises(ValueError, match="explicitly enabled flash attention"):
        validate_cafe_configuration(configuration(kv_cache="turbo4", flash_attention=False))

    with pytest.raises(ValueError, match="explicitly enabled flash attention"):
        validate_cafe_configuration(configuration(kv_cache="turbo4", flash_attention=None))


def test_flash_attention_rejects_integer_truthiness():
    with pytest.raises(ValueError, match="flash_attention must be boolean"):
        validate_cafe_configuration(configuration(flash_attention=1))


def test_hardware_backend_is_not_mistaken_for_runtime_identity():
    from inference_configuration import discover_configurations

    candidates = discover_configurations(
        model={"id": "test-model", "context_length": 4096},
        runtime_profiles=[{"id": "nvidia-profile", "backend": "nvidia", "context_length": 4096}],
    )

    assert candidates == []


def test_icd_rejects_non_integer_context_and_draft_counts():
    from inference_configuration import validate_configuration

    base = {"runtime": "cafe-llama.cpp", "model_ref": "test-model", "context": 4096}
    with pytest.raises(ValueError, match="context"):
        validate_configuration({**base, "context": 1.5})
    with pytest.raises(ValueError, match="context"):
        validate_configuration({**base, "context": "4096"})
    with pytest.raises(ValueError, match="draft_tokens"):
        validate_configuration({**base, "draft_tokens": 1.5})
    with pytest.raises(ValueError, match="draft_tokens"):
        validate_configuration({**base, "draft_tokens": "2"})
