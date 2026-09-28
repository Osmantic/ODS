#!/usr/bin/env python3
"""Model-selection matrix over every hardware envelope the installers see.

Runs scripts/simulate-model-selection.py's simulator (which executes the real
scripts/select-model.py against config/model-library.json) and pins:

* the fleet hosts' defaults, which this selector must never move;
* the tier-map size ceilings the simulator reads for non-Pixel hosts;
* every envelope's pick (tests/fixtures/model-selection-golden.json);
* invariants: every pick is an install recommendation, fits with its memory
  class's margin and serves the 64K Hermes floor itself, file size only
  breaks ties, and a dashboard switch to the pick serves the same context.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENVELOPES = ROOT / "tests" / "fixtures" / "model-selection-envelopes.json"
SIMULATOR = ROOT / "scripts" / "simulate-model-selection.py"
sys.path.insert(0, str(ROOT / "extensions" / "services" / "dashboard-api"))

# Fleet hosts (ODS-Fleet-Qualification STATE.md, 2026-09-25). A change to any
# of these is a fleet default change and needs fleet evidence first.
POLICY = "context-aware-curated-fit-v2"
FLEET_DEFAULTS = {
    # tower1 / tower3, RTX 5090 32 GB (Ubuntu, Pixel default; non-Pixel too).
    # Served at 64K before too: the selector said 32K and Hermes raised it.
    "nv-32gb-ram61-pixel": {"pick": "qwen3.5-27b-q4", "runtime_profile": None, "context_length": 65536,
                            "cache_types": "f16/f16", "policy": POLICY},
    "nv-32gb-ram61-non-pixel": {"pick": "qwen3.5-27b-q4", "runtime_profile": None, "context_length": 65536,
                                "cache_types": "f16/f16", "policy": POLICY},
    # tower2, 2x RTX PRO 6000 (and one card on its own)
    "nv-192gb-ram251-pixel": {"pick": "qwen3-coder-next-q4", "runtime_profile": None, "context_length": 131072,
                              "cache_types": "f16/f16", "policy": POLICY},
    "nv-96gb-ram256-pixel": {"pick": "qwen3-coder-next-q4", "runtime_profile": None, "context_length": 131072,
                             "cache_types": "f16/f16", "policy": POLICY},
    # Strix Halo 128 GB
    "strix-ram124": {"pick": "qwen3.6-35b-a3b-ud-q4", "runtime_profile": None, "context_length": 131072,
                     "cache_types": "f16/f16", "policy": POLICY + "+unified-memory-coder-next-a3b-v1"},
    # mac-mini, M4 16 GB
    "apple-16gb": {"pick": "qwen3.5-9b-q4", "runtime_profile": None, "context_length": 65536,
                   "cache_types": "f16/f16", "policy": POLICY},
    # windows-laptop, RTX 5070 Laptop 8 GB (WSL Pixel host)
    "nv-8gb-ram15-pixel": {"pick": "qwen3.5-9b-q4", "runtime_profile": "nvidia-8gb-64k-q8-kv", "context_length": 65536,
                           "cache_types": "q8_0/q8_0", "policy": POLICY},
    # DGX Spark / GB10 (not in today's fleet; existing Spark policy)
    "nv-unified-ram119": {"pick": "qwen3.6-35b-a3b-ud-q4", "runtime_profile": None, "context_length": 131072,
                          "cache_types": "f16/f16", "policy": POLICY + "+spark-aarch64-nv-ultra-a3b-v1"},
}


def _simulator_module():
    spec = importlib.util.spec_from_file_location("ods_simulate_model_selection", SIMULATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _rows() -> dict[str, dict]:
    module = _simulator_module()
    simulator = module.Simulator(ROOT, ENVELOPES)
    return {row["envelope"]: row for row in simulator.run()}


def _envelopes() -> list[dict]:
    return json.loads(ENVELOPES.read_text(encoding="utf-8"))["envelopes"]


def test_fixture_covers_every_envelope_once():
    envelopes = _envelopes()
    assert len(envelopes) == 65
    assert len({envelope["id"] for envelope in envelopes}) == len(envelopes)
    fleet = {envelope["id"] for envelope in envelopes if envelope["fleet_hosts"]}
    assert set(FLEET_DEFAULTS) <= fleet


@pytest.mark.parametrize("envelope_id", sorted(FLEET_DEFAULTS))
def test_fleet_defaults_do_not_move(envelope_id):
    row = _rows()[envelope_id]
    expected = FLEET_DEFAULTS[envelope_id]
    actual = {key: row.get(key) for key in expected}
    assert actual == expected, (envelope_id, row)


def _selector_env(envelope_id: str) -> dict[str, str]:
    envelope = next(item for item in _envelopes() if item["id"] == envelope_id)
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "select-model.py"),
            "--catalog", str(ROOT / "config" / "model-library.json"),
            "--backend", envelope["backend"], "--memory-type", envelope["memory_type"],
            "--vram-mb", str(envelope["vram_mb"]), "--ram-gb", str(envelope["ram_gb"]),
            "--profile", "qwen", "--tier", str(envelope["tier"]), "--max-size-mb", "0",
            "--host-arch", envelope["host_arch"], "--installable-only",
            "--min-context", "65536", "--env",
        ],
        capture_output=True, text=True, check=True,
    )
    values = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value.strip('"')
    return values


def test_windows_laptop_keeps_its_runtime_contract():
    env = _selector_env("nv-8gb-ram15-pixel")
    assert env["LLM_MODEL"] == "qwen3.5-9b"
    assert env["MODEL_RUNTIME_PROFILE"] == "nvidia-8gb-64k-q8-kv"
    assert env["MAX_CONTEXT"] == "65536"
    assert env["LLAMA_ARG_CACHE_TYPE_K"] == env["LLAMA_ARG_CACHE_TYPE_V"] == "q8_0"
    assert env["LLAMA_SERVER_MEMORY_LIMIT"] == "12G"
    assert env["PIXEL_AGENT_MODEL_READY"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32",
                    reason="sources the tier map with bash")
def test_simulator_reads_the_tier_map_ceilings():
    module = _simulator_module()
    tier_map = ROOT / "installers" / "lib" / "tier-map.sh"
    for envelope in _envelopes():
        if envelope["ceiling"] != "tier-map":
            continue
        sourced = subprocess.run(
            [
                "bash", "-c",
                'error() { :; }; TIER="$1"; HOST_ARCH="$2"; MODEL_PROFILE=qwen; '
                'source "$3"; resolve_tier_config >/dev/null; printf %s "$LLM_MODEL_SIZE_MB"',
                "_", str(envelope["tier"]), envelope["host_arch"], str(tier_map),
            ],
            capture_output=True, text=True, check=True,
        ).stdout
        parsed = module.tier_map_size_mb(tier_map, str(envelope["tier"]), envelope["host_arch"])
        assert parsed == int(sourced), envelope["id"]
        assert _rows()[envelope["id"]]["ceiling_mb"] == int(sourced), envelope["id"]


GOLDEN = ROOT / "tests" / "fixtures" / "model-selection-golden.json"
HERMES_FLOOR = 65536


def _selector_module():
    spec = importlib.util.spec_from_file_location("ods_select_model_for_matrix", ROOT / "scripts" / "select-model.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _catalog() -> dict[str, dict]:
    selector = _selector_module()
    return {model["id"]: model for model in selector.load_catalog(ROOT / "config" / "model-library.json")}


def test_every_envelope_matches_the_golden_file():
    module = _simulator_module()
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = module.golden_view(list(_rows().values()))
    mismatches = sorted(
        envelope for envelope in set(expected) | set(actual)
        if expected.get(envelope) != actual.get(envelope)
    )
    assert not mismatches, [(envelope, expected.get(envelope), actual.get(envelope)) for envelope in mismatches]


@pytest.mark.parametrize("envelope_id", [envelope["id"] for envelope in _envelopes()])
def test_every_pick_fits_and_meets_the_floor(envelope_id):
    row = _rows()[envelope_id]
    model = _catalog()[row["pick"]]
    assert model["install_recommendation"] is True, row["pick"]
    assert row["fits"] is True, row
    assert row["fits_at_hermes"] in (True, "n/a"), row
    assert row["context_length"] <= int(model.get("max_context_length") or model["context_length"]), row
    # Every qwen-profile pick serves the Hermes floor itself: the installers
    # no longer rely on a raise after the fit check.
    assert row["context_length"] >= HERMES_FLOOR, row


@pytest.mark.parametrize("envelope_id", [envelope["id"] for envelope in _envelopes()])
def test_file_size_is_only_a_tie_breaker(envelope_id):
    from model_selection import rank_catalog_models, usable_memory_gb

    row = _rows()[envelope_id]
    if "+" in str(row["policy"]):
        pytest.skip("architecture policy substitution, not a ranking")
    envelope = next(item for item in _envelopes() if item["id"] == envelope_id)
    capacity, _label = usable_memory_gb(
        envelope["backend"], envelope["memory_type"], envelope["vram_mb"], envelope["ram_gb"],
    )
    ranked = rank_catalog_models(
        _catalog().values(), capacity_gb=capacity, profile="qwen", installable_only=True,
        backend=envelope["backend"], memory_type=envelope["memory_type"],
        vram_mb=envelope["vram_mb"], ram_gb=envelope["ram_gb"], host_arch=envelope["host_arch"],
        max_size_mb=row["ceiling_mb"], min_context=HERMES_FLOOR, include_size_tiebreak=False,
    )
    assert ranked and ranked[0].id == row["pick"], (envelope_id, [c.id for c in ranked[:3]])


@pytest.mark.parametrize("envelope_id", [envelope["id"] for envelope in _envelopes()])
def test_a_switch_serves_what_the_installer_serves(envelope_id):
    """The dashboard's switch policy is the installer's, for every envelope.

    routers/models.py plans a load through performance_oracle ->
    model_selection.plan_model_context, which runs plan_candidate: the code
    select-model.py ranks with. Loading the installer's pick on the same
    hardware must give the same context and runtime profile.
    """
    from model_selection import plan_model_context, usable_memory_gb

    row = _rows()[envelope_id]
    envelope = next(item for item in _envelopes() if item["id"] == envelope_id)
    capacity, _label = usable_memory_gb(
        envelope["backend"], envelope["memory_type"], envelope["vram_mb"], envelope["ram_gb"],
    )
    plan = plan_model_context(
        _catalog()[row["pick"]], capacity_gb=capacity, backend=envelope["backend"],
        memory_type=envelope["memory_type"], vram_mb=envelope["vram_mb"], ram_gb=envelope["ram_gb"],
        host_arch=envelope["host_arch"], min_context=HERMES_FLOOR,
    )
    assert plan["fits"] is True, (envelope_id, plan)
    assert (plan["context_length"], plan["runtime_profile"]) == (row["context_length"], row["runtime_profile"]), (
        envelope_id, plan, row,
    )
    # A restore that starts from a context recorded below the floor (the
    # tower1/tower3 MODEL_RECOMMENDED_CONTEXT=32768) still lands on it.
    below = plan_model_context(
        _catalog()[row["pick"]], capacity_gb=capacity, backend=envelope["backend"],
        memory_type=envelope["memory_type"], vram_mb=envelope["vram_mb"], ram_gb=envelope["ram_gb"],
        host_arch=envelope["host_arch"], min_context=HERMES_FLOOR, preferred_context=32768,
    )
    assert below["context_length"] >= HERMES_FLOOR, (envelope_id, below)


def _plan_on_nvidia(model_id: str, vram_mb: int, preferred: int | None) -> dict:
    from model_selection import plan_model_context

    return plan_model_context(
        _catalog()[model_id], capacity_gb=vram_mb / 1024.0, backend="nvidia",
        memory_type="discrete", vram_mb=vram_mb, ram_gb=64, host_arch="amd64",
        min_context=HERMES_FLOOR, preferred_context=preferred,
    )


@pytest.mark.parametrize(
    ("model_id", "vram_mb", "preferred"),
    [
        # A stale CTX_SIZE / MODEL_RECOMMENDED_CONTEXT above the native
        # context (phi-4: 16,384; Qwen3-30B-A3B: 40,960, the #6712 rollback)
        # must not be replayed: llama.cpp caps the slot at the training
        # context, so the activation's context proof could never pass.
        ("phi4-q4", 24576, 65536),
        ("phi4-q4", 16384, 32768),
        ("qwen3-30b-a3b-q4", 49140, 131072),
    ],
)
def test_a_switch_never_plans_above_the_declared_native_context(model_id, vram_mb, preferred):
    plan = _plan_on_nvidia(model_id, vram_mb, preferred)
    native = int(_catalog()[model_id]["max_context_length"])
    assert plan["context_length"] <= native, plan
    assert plan["max_context_length"] == native, plan
    assert plan["meets_min_context"] is False, plan


def test_a_switch_keeps_an_owner_context_within_the_native_context():
    # tower2: an owner's 262,144 for Qwen3-Coder-Next is its native context,
    # so it is kept rather than clamped to the 131,072 catalog default.
    plan = _plan_on_nvidia("qwen3-coder-next-q4", 195774, 262144)
    assert plan["fits"] is True and plan["context_length"] == 262144, plan


def test_the_hermes_recheck_refuses_a_raise_above_the_native_context():
    """select-model.py --check-fit (phase 03's Hermes re-check).

    phi-4 needs ~21.4 GiB at 64K, so memory alone says it fits a 24 GB card;
    its native context is 16,384, so the raise can never be served.
    """
    from model_selection import check_fit

    phi4 = _catalog()["phi4-q4"]
    raised = check_fit(phi4, context_length=HERMES_FLOOR, capacity_gb=24.0, mclass="discrete")
    assert raised["fits"] is False and raised["above_native_max"] is True, raised
    native = check_fit(phi4, context_length=16384, capacity_gb=24.0, mclass="discrete")
    assert native["fits"] is True and native["above_native_max"] is False, native
    qwen3_30b = check_fit(
        _catalog()["qwen3-30b-a3b-q4"], context_length=HERMES_FLOOR, capacity_gb=48.0, mclass="discrete",
    )
    assert qwen3_30b["fits"] is False and qwen3_30b["above_native_max"] is True, qwen3_30b


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
