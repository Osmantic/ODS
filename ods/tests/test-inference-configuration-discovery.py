import pytest

from cafe_llama_icd import CAPABILITIES, configuration_to_env, validate_cafe_configuration
from inference_configuration import (
    configuration_signature,
    rank_measured_candidates,
)
from model_selection import Candidate


class FakeEstimate:
    method = "runtime-profile"

    def as_dict(self):
        return {}


def make_candidate():
    model = {
        "id": "test-cafe-model",
        "context_length": 8192,
    }

    profile = {
        "id": "cafe-test",
        "runtime": "cafe-llama.cpp",
        "runtime_revision": "test",
        "kernel": "ptq1-mmV",
        "quantization": "PTQ1_0",
        "context_length": 8192,
        "gpu_layers": 99,
        "kv_cache": "f16",
        "flash_attention": True,
        "offload": "host-moe",
        "speculation": "draft-mtp",
        "draft_tokens": 2,
        "batch": 1,
    }

    model["runtime_profiles"] = [profile]

    return Candidate(
        model=model,
        runtime_profile=profile,
        context_length=8192,
        required_gb=1.0,
        estimate=FakeEstimate(),
        architecture_estimate=False,
        authored_estimate=True,
        meets_min_context=True,
        memory_class="discrete",
        capacity_gb=4.0,
        fit_margin_gb=0.25,
        priority=10,
        evidence=0,
    )


def test_candidate_discovers_base_configuration():
    candidate = make_candidate()

    configs = candidate.inference_configurations()

    assert len(configs) == 1

    data = configs[0].to_dict()

    assert data["measurement_required"] is True
    assert data["execution_authorized"] is False
    assert data["configuration_id"] == configuration_signature(
        data["configuration"]
    )


def test_candidate_without_runtime_profile_returns_no_configurations():
    candidate = make_candidate()

    candidate = Candidate(
        model=candidate.model,
        runtime_profile=None,
        context_length=candidate.context_length,
        required_gb=candidate.required_gb,
        estimate=candidate.estimate,
        architecture_estimate=candidate.architecture_estimate,
        authored_estimate=candidate.authored_estimate,
        meets_min_context=candidate.meets_min_context,
        memory_class=candidate.memory_class,
        capacity_gb=candidate.capacity_gb,
        fit_margin_gb=candidate.fit_margin_gb,
        priority=candidate.priority,
        evidence=candidate.evidence,
    )

    assert candidate.inference_configurations() == []


def test_cafe_configuration_maps_to_environment():
    candidate = make_candidate()
    configuration = candidate.inference_configurations()[0].configuration

    validate_cafe_configuration(configuration)
    env = configuration_to_env(configuration)

    assert env == {
        "CAFE_LLAMA_ENABLED": "true",
        "LLAMA_ARG_N_GPU_LAYERS": "99",
        "MAX_CONTEXT": "8192",
        "LLAMA_ARG_CACHE_TYPE_K": "f16",
        "LLAMA_ARG_FLASH_ATTN": "on",
        "LLAMA_ARG_SPEC_TYPE": "draft-mtp",
        "LLAMA_ARG_SPEC_DRAFT_N_MAX": "2",
        "LLAMA_ARG_HOST_MOE": "on",
    }


def test_cafe_rejects_invalid_configurations():
    candidate = make_candidate()
    configuration = candidate.inference_configurations()[0].configuration

    turbo_without_flash = dict(configuration)
    turbo_without_flash["kv_cache"] = "turbo4"
    turbo_without_flash["flash_attention"] = False

    with pytest.raises(ValueError, match="turbo KV"):
        validate_cafe_configuration(turbo_without_flash)

    missing_draft_tokens = dict(configuration)
    missing_draft_tokens["speculation"] = "draft-mtp"
    missing_draft_tokens["draft_tokens"] = 0

    with pytest.raises(ValueError, match="draft-mtp"):
        validate_cafe_configuration(missing_draft_tokens)


def test_icd_expands_runtime_capabilities_deterministically():
    candidate = make_candidate()

    configs = candidate.inference_configurations(CAPABILITIES)

    assert len(configs) == 160

    ids = [item.configuration_id for item in configs]

    assert len(ids) == len(set(ids))
    assert ids == [
        item.configuration_id
        for item in candidate.inference_configurations(CAPABILITIES)
    ]

    for item in configs:
        data = item.to_dict()

        assert data["measurement_required"] is True
        assert data["execution_authorized"] is False
        assert "measured_tps" not in data["configuration"]


def test_measured_evidence_beats_unmeasured_candidates():
    candidate = make_candidate()
    discovered = candidate.inference_configurations(CAPABILITIES)

    candidate_dicts = [item.to_dict() for item in discovered]

    winner = candidate_dicts[7]
    runner_up = candidate_dicts[11]

    measurements = [
        {
            "configuration_id": winner["configuration_id"],
            "evidence_type": "measured",
            "measured_tps": 77.0,
        },
        {
            "configuration_id": runner_up["configuration_id"],
            "evidence_type": "measured",
            "measured_tps": 12.0,
        },
        {
            "configuration_id": candidate_dicts[20]["configuration_id"],
            "evidence_type": "estimated",
            "measured_tps": 999.0,
        },
    ]

    ranked = rank_measured_candidates(candidate_dicts, measurements)

    assert [item["configuration_id"] for item in ranked] == [
        winner["configuration_id"],
        runner_up["configuration_id"],
    ]
    assert ranked[0]["measured_tps"] == 77.0
    assert ranked[0]["selection_status"] == "MEASURED"
