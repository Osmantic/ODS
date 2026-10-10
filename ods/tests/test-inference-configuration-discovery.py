import pytest

from cafe_llama_icd import (
    CAPABILITIES,
    DISCOVERY_CAPABILITIES,
    parameter_catalog,
    validate_cafe_configuration,
)
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
        "kernel": "baseline",
        "quantization": "Q4_K_M",
        "context_length": 8192,
        "gpu_layers": 99,
        "kv_cache": "f16",
        "flash_attention": True,
        "offload": "none",
        "speculation": "none",
        "draft_tokens": 0,
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


def test_catalog_exposes_documented_and_build_dependent_capabilities():
    candidate = make_candidate()
    configuration = candidate.inference_configurations()[0].configuration
    catalog = parameter_catalog()

    assert "host_moe" in catalog
    assert "ssd_streaming" in catalog
    assert "ngram_ssd" in catalog
    assert "turbo_kv" in catalog
    assert "spec_type" in catalog
    assert "runtime_provenance" in catalog
    assert "turbo4" in CAPABILITIES["kv_cache"]

    # Catalog-known values are discoverable; a probed binary must still gate execution.
    turbo = dict(configuration)
    turbo["kv_cache"] = "turbo4"
    validate_cafe_configuration(turbo)
    with pytest.raises(ValueError, match="does not advertise kv_cache"):
        validate_cafe_configuration(
            turbo,
            runtime_capabilities={
                "kernel": ["baseline"],
                "kv_cache": ["f16", "q8_0"],
                "flash_attention": [True, False],
                "offload": ["none"],
                "speculation": ["none"],
            },
        )

    moe = dict(configuration)
    moe["offload"] = "host-moe"
    validate_cafe_configuration(moe)
    with pytest.raises(ValueError, match="does not advertise offload"):
        validate_cafe_configuration(
            moe,
            runtime_capabilities={
                "kernel": ["baseline"],
                "kv_cache": ["f16", "q8_0"],
                "flash_attention": [True, False],
                "offload": ["none"],
                "speculation": ["none"],
            },
        )


def test_cafe_rejects_invalid_scalar_values():
    candidate = make_candidate()
    configuration = candidate.inference_configurations()[0].configuration

    invalid_context = dict(configuration)
    invalid_context["context"] = 0
    with pytest.raises(ValueError, match="context"):
        validate_cafe_configuration(invalid_context)

    invalid_layers = dict(configuration)
    invalid_layers["gpu_layers"] = -1
    with pytest.raises(ValueError, match="gpu_layers"):
        validate_cafe_configuration(invalid_layers)

    invalid_draft = dict(configuration)
    invalid_draft["draft_tokens"] = 2
    with pytest.raises(ValueError, match="draft tokens"):
        validate_cafe_configuration(invalid_draft)

    boolean_draft = dict(configuration)
    boolean_draft["draft_tokens"] = False
    with pytest.raises(ValueError, match="draft_tokens"):
        validate_cafe_configuration(boolean_draft)

    non_boolean_flash_attention = dict(configuration)
    non_boolean_flash_attention["flash_attention"] = 1
    with pytest.raises(ValueError, match="flash_attention"):
        validate_cafe_configuration(non_boolean_flash_attention)


def test_icd_expands_only_verified_runtime_capabilities_deterministically():
    candidate = make_candidate()

    configs = candidate.inference_configurations(DISCOVERY_CAPABILITIES)

    assert len(configs) == 4

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
        validate_cafe_configuration(data["configuration"])


def test_measured_evidence_beats_unmeasured_candidates():
    candidate = make_candidate()
    discovered = candidate.inference_configurations(CAPABILITIES)

    candidate_dicts = [item.to_dict() for item in discovered]

    winner = candidate_dicts[1]
    runner_up = candidate_dicts[2]

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
            "configuration_id": candidate_dicts[3]["configuration_id"],
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
