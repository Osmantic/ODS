"""Full GPU residency: estimator, planner, placement parser, and tier defaults.

The numbers asserted here are llama.cpp b9014 load-log values from the fleet
(2026-09-25): the 8GB RTX 5070 Laptop under WSL (Qwen3.5 9B/4B) and the RTX
5090 native-Linux towers (Qwen3.5 27B). The estimator must reproduce
llama.cpp's own ``projected to use`` line, and every default the installer
picks for an NVIDIA tier must stay fully on the GPU.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from model_memory import (
    estimated_device_memory_mib,
    gpu_residency_fit,
    is_llama_ready_line,
    is_placement_log_line,
    parse_llama_placement,
    performance_core_count,
    plan_residency_fallback,
    platform_reserve_mib,
    resident_configuration,
    runtime_memory_settings,
)

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "config" / "model-library.json"

# llama.cpp b9014 projections measured on the fleet.
QWEN35_9B = {
    "id": "qwen3.5-9b-q4",
    "size_mb": 5760,
    "vram_required_gb": 8,
    "context_length": 65536,
    "gpu_residency": {
        "basis": "measured",
        "gpu_weights_mib": 4861.28,
        "kv_bytes_per_token_f16": 32768,
        "recurrent_state_mib": 50.25,
        "vocab_size": 248320,
        "embedding_length": 4096,
    },
}
LAPTOP_TOTAL_MIB = 8151  # nvidia-smi memory.total, RTX 5070 Laptop
LAPTOP_CUDA_FREE_MIB = 6860  # free device memory llama.cpp saw at every load


def _load_selector():
    spec = importlib.util.spec_from_file_location("select_model_residency", ROOT / "scripts" / "select-model.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _catalog_by_id():
    return {model["id"]: model for model in json.loads(CATALOG.read_text(encoding="utf-8"))["models"]}


class TestEstimatorMatchesLlamaCpp:
    @pytest.mark.parametrize(
        ("cache", "ubatch", "expected"),
        [("q8_0", 512, 6492), ("q8_0", 256, 6246), ("q4_0", 512, 5980)],
    )
    def test_qwen35_9b_projection_matches_laptop_load_logs(self, cache, ubatch, expected):
        projection = estimated_device_memory_mib(
            QWEN35_9B, context_length=65536, cache_type_k=cache, cache_type_v=cache, ubatch=ubatch,
        )
        assert projection["basis"] == "measured"
        assert abs(projection["totalMiB"] - expected) < 1.0

    def test_qwen35_27b_projection_matches_tower_load_log(self):
        model = _catalog_by_id()["qwen3.5-27b-q4"]
        projection = estimated_device_memory_mib(model, context_length=65536)
        assert abs(projection["totalMiB"] - 20013) < 1.0

    def test_qwen35_4b_projection_matches_benchmark(self):
        model = _catalog_by_id()["qwen3.5-4b-q4"]
        projection = estimated_device_memory_mib(
            model, context_length=65536, cache_type_k="q8_0", cache_type_v="q8_0",
        )
        assert abs(projection["totalMiB"] - 4231) < 1.0

    @pytest.mark.parametrize(
        ("total", "platform", "measured_loss"),
        [(32607, "linux", 1176), (97887, "linux", 1438), (8151, "wsl", 1291)],
    )
    def test_platform_reserve_covers_every_calibration_point(self, total, platform, measured_loss):
        assert platform_reserve_mib(total, platform) >= measured_loss

    def test_declared_models_keep_their_vram_class(self):
        model = {"id": "unknown-7b", "size_mb": 1000, "vram_required_gb": 7, "context_length": 8192}
        projection = estimated_device_memory_mib(model, context_length=8192)
        assert projection["basis"] == "declared"
        small = gpu_residency_fit(model, total_vram_mb=6144, gpu_platform="linux")
        assert small["fits"] is False  # declared "needs a 7GB GPU"
        assert small["requiredGb"] >= 7
        large = gpu_residency_fit(model, total_vram_mb=8192, gpu_platform="linux")
        assert large["fits"] is True


class TestLaptopRegression:
    def test_previous_profile_settings_could_not_stay_on_the_gpu(self):
        # ubatch 512 and llama.cpp's default 1024 MiB fit target: the shipped
        # profile loaded 29/33 layers on every load of the laptop.
        fit = gpu_residency_fit(
            QWEN35_9B,
            total_vram_mb=LAPTOP_TOTAL_MIB,
            env={"LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0"},
            context_length=65536,
            gpu_platform="wsl",
        )
        assert fit["fits"] is False
        assert fit["projection"]["totalMiB"] + 1024 > LAPTOP_CUDA_FREE_MIB

    def test_shipped_profile_stays_on_the_gpu_with_measured_margin(self):
        model = _catalog_by_id()["qwen3.5-9b-q4"]
        profile = next(p for p in model["runtime_profiles"] if p["id"] == "nvidia-8gb-64k-q8-kv")
        assert profile["env"]["LLAMA_ARG_UBATCH"] == "256"
        assert profile["env"]["LLAMA_ARG_FIT_TARGET"] == "512"
        fit = gpu_residency_fit(model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl")
        assert fit["fits"] is True
        # Cross-check against what CUDA actually reported free at load.
        assert fit["projection"]["totalMiB"] + 512 <= LAPTOP_CUDA_FREE_MIB
        # The platform budget is never more generous than the measured one.
        assert fit["availableMiB"] <= LAPTOP_CUDA_FREE_MIB

    def test_planner_reproduces_the_benchmarked_fix_from_the_partial_load(self):
        plan = plan_residency_fallback(
            required_mib=6492,
            available_mib=LAPTOP_CUDA_FREE_MIB,
            settings={"ubatch": 512, "fitTargetMiB": 1024, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088,
            compute_mib=493,
            context_length=65536,
        )
        assert plan is not None
        assert plan["changes"] == {"LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512"}
        assert plan["contextLength"] == 65536


class TestFallbackPlanner:
    base = {"ubatch": 256, "fitTargetMiB": 512, "cacheTypeK": "f16", "cacheTypeV": "f16"}

    def test_order_is_q8_then_context_then_q4(self):
        plan = plan_residency_fallback(
            required_mib=9000, available_mib=7000, settings=self.base,
            kv_mib=4096, compute_mib=250, context_length=131072,
        )
        assert plan["steps"] == ["KV cache q8_0", "context 65536"]
        assert plan["changes"]["LLAMA_ARG_FLASH_ATTN"] == "on"
        assert plan["changes"]["CTX_SIZE"] == "65536"

    def test_context_never_drops_below_the_agent_floor(self):
        plan = plan_residency_fallback(
            required_mib=12000, available_mib=7000, settings=self.base,
            kv_mib=4096, compute_mib=250, context_length=65536,
        )
        assert plan is None

    def test_explicit_context_is_never_reduced(self):
        plan = plan_residency_fallback(
            required_mib=9000, available_mib=7000, settings=self.base,
            kv_mib=4096, compute_mib=250, context_length=131072,
            allow_context_reduction=False,
        )
        assert "CTX_SIZE" not in plan["changes"]
        assert plan["cacheTypeK"] == "q4_0"

    def test_fitting_configuration_needs_no_change(self):
        plan = plan_residency_fallback(
            required_mib=4000, available_mib=7000, settings=self.base,
            kv_mib=500, compute_mib=250, context_length=65536,
        )
        assert plan["changes"] == {}


class TestRuntimeSettings:
    def test_precedence_is_overrides_profile_env_default(self):
        settings = runtime_memory_settings(
            {"env": {"LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_UBATCH": "256"}},
            {"LLAMA_ARG_CACHE_TYPE_K": "f16", "LLAMA_ARG_FIT_TARGET": "768"},
            {"LLAMA_ARG_UBATCH": "128"},
        )
        assert settings["cacheTypeK"] == "q8_0"
        assert settings["ubatch"] == 128
        assert settings["fitTargetMiB"] == 768
        assert settings["cacheTypeV"] == "f16"

    def test_declared_moe_offload_is_intentional(self):
        assert runtime_memory_settings({"env": {"LLAMA_ARG_N_CPU_MOE": "24"}})["intentionalOffload"] is True
        assert runtime_memory_settings({"env": {"LLAMA_ARG_N_CPU_MOE": "0"}})["intentionalOffload"] is False


def _load_log(on, total, *, cpu_kv=0.0, projected=6492, free=6860, target=1024, model="/models/Qwen3.5-9B-Q4_K_M.gguf", met=False):
    fit = (
        f"common_params_fit_impl: will leave {free - projected} >= {target} MiB of free device memory, no changes needed"
        if met
        else f"common_params_fit_impl: cannot meet free memory target of {target} MiB, need to reduce device memory by {projected + target - free} MiB"
    )
    lines = [
        "ggml_cuda_init: found 1 CUDA devices (Total VRAM: 8150 MiB):",
        f"common_params_fit_impl: projected to use {projected} MiB of device memory vs. {free} MiB of free device memory",
        fit,
        f"llama_model_loader: loaded meta data with 46 key-value pairs and 427 tensors from {model} (version GGUF V3 (latest))",
        "print_info: n_embd                = 4096",
        "print_info: n_vocab               = 248320",
        f"load_tensors: offloaded {on}/{total} layers to GPU",
        "load_tensors:   CPU_Mapped model buffer size =  545.62 MiB",
        "load_tensors:        CUDA0 model buffer size =  4861.28 MiB",
    ]
    if cpu_kv:
        lines.append(f"llama_kv_cache:        CPU KV buffer size =   {cpu_kv:.2f} MiB")
    lines += [
        f"llama_kv_cache:      CUDA0 KV buffer size =  {1088 - cpu_kv:.2f} MiB",
        "llama_memory_recurrent:      CUDA0 RS buffer size =    50.25 MiB",
        "sched_reserve:      CUDA0 compute buffer size =   493.00 MiB",
        "sched_reserve:  CUDA_Host compute buffer size =   152.38 MiB",
        "srv  update_slots: all slots are idle",
    ]
    return "\n".join(lines) + "\n"


class TestPlacementParser:
    def test_partial_load_is_reported_with_fit_numbers(self):
        placement = parse_llama_placement(_load_log(29, 33, cpu_kv=136), expected_model_file="Qwen3.5-9B-Q4_K_M.gguf")
        assert placement["status"] == "partial"
        assert placement["fullyResident"] is False
        assert (placement["layersOnGpu"], placement["layersTotal"]) == (29, 33)
        assert placement["cpuKvMiB"] == 136
        assert placement["projectedDeviceMiB"] == 6492
        assert placement["freeDeviceMiB"] == 6860
        assert placement["fitTargetMiB"] == 1024
        assert placement["kvMiB"] == 1088
        assert placement["gpuComputeMiB"] == 493
        assert "29/33 layers" in placement["reason"]

    def test_full_load_ignores_host_token_embedding(self):
        placement = parse_llama_placement(
            _load_log(33, 33, projected=6246, target=512, met=True),
            expected_model_file="Qwen3.5-9B-Q4_K_M.gguf",
        )
        assert placement["status"] == "fully_resident"
        assert placement["fullyResident"] is True
        assert placement["cpuWeightMiB"] == pytest.approx(545.62)
        assert placement["fitTargetMiB"] == 512

    def test_most_recent_load_of_the_expected_model_wins(self):
        text = (
            _load_log(29, 33, cpu_kv=136)
            + _load_log(33, 33, model="/models/other.gguf", projected=4231, met=True)
            + _load_log(33, 33, projected=6246, target=512, met=True)
        )
        placement = parse_llama_placement(text, expected_model_file="Qwen3.5-9B-Q4_K_M.gguf")
        assert placement["status"] == "fully_resident"
        assert placement["projectedDeviceMiB"] == 6246
        other = parse_llama_placement(text, expected_model_file="other.gguf")
        assert other["projectedDeviceMiB"] == 4231
        missing = parse_llama_placement(text, expected_model_file="absent.gguf")
        assert missing["status"] == "unverified"

    def test_log_without_offload_line_is_unverified_not_resident(self):
        placement = parse_llama_placement("srv  load_model: loading model\n")
        assert placement["status"] == "unverified"
        assert placement["fullyResident"] is None

    def test_declared_moe_offload_is_an_allowed_exception(self):
        placement = parse_llama_placement(_load_log(33, 33, met=True), intentional_offload=True)
        assert placement["status"] == "intentional_offload"
        assert placement["intentionalOffload"] is True

    def test_undeclared_layer_loss_is_never_intentional(self):
        placement = parse_llama_placement(_load_log(29, 33, cpu_kv=136), intentional_offload=True)
        assert placement["status"] == "partial"


class TestPerformanceCores:
    def _cpu(self, root, cpu, core, package="0", capacity=None):
        topology = root / "devices" / "system" / "cpu" / f"cpu{cpu}" / "topology"
        topology.mkdir(parents=True, exist_ok=True)
        (topology / "core_id").write_text(str(core))
        (topology / "physical_package_id").write_text(package)
        if capacity is not None:
            (topology.parent / "cpu_capacity").write_text(str(capacity))

    def test_intel_hybrid_counts_physical_p_cores(self, tmp_path):
        for cpu in range(12):
            self._cpu(tmp_path, cpu, cpu // 2 if cpu < 8 else cpu)
        (tmp_path / "devices" / "cpu_core").mkdir(parents=True)
        (tmp_path / "devices" / "cpu_core" / "cpus").write_text("0-7\n")
        (tmp_path / "devices" / "system" / "cpu" / "online").write_text("0-11\n")
        assert performance_core_count(sysfs_root=str(tmp_path), cpuinfo_path=str(tmp_path / "none"), system="linux") == 4

    def test_big_little_keeps_the_fast_cluster(self, tmp_path):
        for cpu in range(8):
            self._cpu(tmp_path, cpu, cpu, capacity=1024 if cpu >= 4 else 400)
        (tmp_path / "devices" / "system" / "cpu" / "online").write_text("0-7\n")
        assert performance_core_count(sysfs_root=str(tmp_path), cpuinfo_path=str(tmp_path / "none"), system="linux") == 4

    def test_hidden_hybrid_split_does_not_reach_efficiency_cores(self, tmp_path):
        # Core Ultra 9 285H under WSL: 16 cores, P/E split hidden. The
        # benchmark measured 6 threads 10% faster than 4 and 14 threads 64%
        # slower; the estimate must stay near the six P-cores.
        for cpu in range(16):
            self._cpu(tmp_path, cpu, cpu)
        (tmp_path / "devices" / "system" / "cpu" / "online").write_text("0-15\n")
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text("processor\t: 0\nflags\t\t: fpu sse sse2 avx2 hybrid_cpu\n")
        assert performance_core_count(sysfs_root=str(tmp_path), cpuinfo_path=str(cpuinfo), system="linux") == 6


# Every default the installer picks for an NVIDIA VRAM tier must stay fully
# on the GPU. installers/phases/02-detection.sh runs the selector without
# --agent-ready-only and, for the Pixel default, without a size ceiling; that
# is the route below. Identities are the defaults before residency was
# enforced, except 4GB: no capacity-ranked model can stay on a 4GB card
# (Phi-4 mini needs 2376 MiB of weights, 1024 MiB of KV at 8K and 397 MiB of
# compute against at most ~2.9 GB CUDA-usable), so the selector falls back to
# the best model that can.
PRE_RESIDENCY_DEFAULTS = {
    4096: ("phi4-mini-q4", 8192),
    6144: ("phi4-mini-q4", 16384),
    8151: ("qwen3.5-9b-q4", 65536),
    8192: ("qwen3.5-9b-q4", 65536),
    10240: ("qwen3.5-9b-q4", 65536),
    12282: ("phi4-q4", 16384),
    16376: ("phi4-q4", 16384),
    20480: ("qwen3.5-27b-q4", 32768),
    24564: ("qwen3.5-27b-q4", 32768),
    32607: ("qwen3.5-27b-q4", 32768),
    49140: ("deepseek-r1-70b-q4", 32768),
    97887: ("qwen3-coder-next-q4", 131072),
    195774: ("qwen3-coder-next-q4", 131072),
}
TIER_TABLE = [
    # vram_mb (sum over GPUs), platform, gpu count, model id, context, residency settings
    (4096, "linux", 1, "qwen3.5-2b-q4", 65536, {"LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512"}),
    (4096, "wsl", 1, "qwen3.5-2b-q4", 65536, {
        "LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512",
        "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on",
    }),
    (6144, "linux", 1, "phi4-mini-q4", 16384, {
        "LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512",
        "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on",
    }),
    # 8GB: the runtime profile itself carries ubatch 256 / fit target 512.
    (8151, "wsl", 1, "qwen3.5-9b-q4", 65536, {}),
    (8151, "linux", 1, "qwen3.5-9b-q4", 65536, {}),
    (8192, "windows", 1, "qwen3.5-9b-q4", 65536, {}),
    (10240, "linux", 1, "qwen3.5-9b-q4", 65536, {}),
    (12282, "linux", 1, "phi4-q4", 16384, {
        "LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512",
        "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on",
    }),
    (12282, "wsl", 1, "phi4-q4", 16384, {
        "LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512",
        "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on",
    }),
    (16376, "linux", 1, "phi4-q4", 16384, {}),
    (20480, "wsl", 1, "qwen3.5-27b-q4", 32768, {}),
    (24564, "linux", 1, "qwen3.5-27b-q4", 32768, {}),
    (32607, "linux", 1, "qwen3.5-27b-q4", 32768, {}),
    (32607, "wsl", 1, "qwen3.5-27b-q4", 32768, {}),
    # 70B at 32K with an f16 KV cache needs ~50.5 GB: more than the card.
    (49140, "linux", 1, "deepseek-r1-70b-q4", 32768, {
        "LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512",
        "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on",
    }),
    (97887, "linux", 1, "qwen3-coder-next-q4", 131072, {}),
    (195774, "linux", 2, "qwen3-coder-next-q4", 131072, {}),
]


def _installer_selection(vram_mb, platform, gpu_count, *, agent_ready_only=False, ram_gb=32):
    selector = _load_selector()
    catalog = selector.load_catalog(CATALOG)
    capacity, _ = selector.usable_memory_gb("nvidia", "discrete", vram_mb, ram_gb)
    ranked = selector.rank_models(
        catalog, capacity, "qwen", True, "nvidia", "discrete", vram_mb, ram_gb, "amd64",
        agent_ready_only=agent_ready_only, gpu_platform=platform, gpu_count=gpu_count,
    )
    return selector, ranked


@pytest.mark.parametrize(("vram_mb", "platform", "gpu_count", "model_id", "context", "settings"), TIER_TABLE)
def test_nvidia_tier_defaults_are_fully_gpu_resident(vram_mb, platform, gpu_count, model_id, context, settings):
    selector, ranked = _installer_selection(vram_mb, platform, gpu_count)
    selected = ranked[0]
    runtime_profile = selected.get("_runtime_profile")
    assert (selected["id"], selector.effective_context_length(selected, runtime_profile)) == (model_id, context)
    if vram_mb != 4096:
        # Residency changes settings, never the tier's model or context.
        assert (model_id, context) == PRE_RESIDENCY_DEFAULTS[vram_mb]
    residency = selected["_gpu_residency"]
    assert residency["fits"] is True, residency
    assert residency["headroomMiB"] >= 0
    assert selected["_residency_overrides"] == settings
    # Re-check independently with the chosen settings applied.
    confirm = gpu_residency_fit(
        selected,
        total_vram_mb=vram_mb,
        runtime_profile=runtime_profile,
        context_length=context,
        gpu_platform=platform,
        gpu_count=gpu_count,
        overrides=selected.get("_residency_overrides"),
    )
    assert confirm["fits"] is True


@pytest.mark.parametrize(("vram_mb", "platform"), [(8151, "wsl"), (12282, "linux"), (24564, "linux"), (97887, "linux")])
def test_pixel_agent_route_stays_resident_at_the_agent_floor(vram_mb, platform):
    selector, ranked = _installer_selection(vram_mb, platform, 1, agent_ready_only=True)
    selected = ranked[0]
    runtime_profile = selected.get("_runtime_profile")
    assert selected["id"] == "qwen3.5-9b-q4"
    assert selector.effective_context_length(selected, runtime_profile) >= 65536
    assert selected["_gpu_residency"]["fits"] is True


def test_4gb_default_change_is_forced_by_residency():
    """The only identity change: the previous 4GB default cannot be resident."""
    model = _catalog_by_id()["phi4-mini-q4"]
    for platform in ("linux", "wsl"):
        config = resident_configuration(
            model, total_vram_mb=4096, context_length=8192, gpu_platform=platform,
        )
        assert config["fits"] is False
        # Even with no KV cache at all, weights and compute exceed the budget.
        weights_and_compute = config["residency"]["projection"]["weightsMiB"] + 198
        assert weights_and_compute > config["residency"]["availableMiB"] - 512


def test_selector_env_carries_the_residency_settings(tmp_path):
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "select-model.py"),
            "--catalog", str(CATALOG), "--backend", "nvidia", "--memory-type", "discrete",
            "--vram-mb", "12282", "--ram-gb", "32", "--profile", "qwen", "--tier", "2",
            "--host-arch", "amd64", "--installable-only", "--platform", "linux", "--env",
        ],
        capture_output=True, text=True, check=True,
    )
    env = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert env["GGUF_FILE"] == '"phi-4-Q4_K_M.gguf"'
    assert env["MAX_CONTEXT"] == '"16384"'
    assert env["LLAMA_ARG_CACHE_TYPE_K"] == '"q8_0"'
    assert env["LLAMA_ARG_CACHE_TYPE_V"] == '"q8_0"'
    assert env["LLAMA_ARG_FLASH_ATTN"] == '"on"'
    assert env["LLAMA_ARG_UBATCH"] == '"256"'
    assert env["LLAMA_ARG_FIT_TARGET"] == '"512"'


def test_8gb_laptop_default_keeps_its_identity_and_profile():
    selector, ranked = _installer_selection(LAPTOP_TOTAL_MIB, "wsl", 1, ram_gb=31)
    selected = ranked[0]
    assert selected["id"] == "qwen3.5-9b-q4"
    assert selected["_runtime_profile"]["id"] == "nvidia-8gb-64k-q8-kv"
    assert selected["_residency_overrides"] == {}


def test_exact_metadata_outranks_a_hand_typed_profile_number():
    """The 9B's 7.2GB profile estimate no longer decides the fit."""
    model = _catalog_by_id()["qwen3.5-9b-q4"]
    profile = next(p for p in model["runtime_profiles"] if p["id"] == "nvidia-8gb-64k-q8-kv")
    fit = gpu_residency_fit(model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl")
    assert fit["projection"]["basis"] == "measured"
    assert abs(fit["projection"]["totalMiB"] - 6246) < 1.0


def test_known_non_resident_8gb_profiles_are_not_offered_as_fitting():
    catalog = _catalog_by_id()
    for model_id, profile_id in (
        ("ministral3-8b-instruct-2512-q4", "nvidia-8gb-64k-q4-kv"),
        ("granite3.3-8b-instruct-q4", "nvidia-8gb-64k"),
    ):
        model = catalog[model_id]
        profile = next(p for p in model["runtime_profiles"] if p["id"] == profile_id)
        config = resident_configuration(
            model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl",
        )
        assert config["fits"] is False, model_id


def test_other_gpu_users_are_budgeted():
    model = _catalog_by_id()["qwen3.5-9b-q4"]
    profile = next(p for p in model["runtime_profiles"] if p["id"] == "nvidia-8gb-64k-q8-kv")
    idle = resident_configuration(model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl")
    busy = resident_configuration(
        model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl",
        other_used_mib=400,
    )
    assert idle["fits"] and idle["overrides"] == {}
    # 400 MiB held elsewhere (a desktop on the dGPU, Whisper): q4_0 KV keeps
    # every layer on the GPU at the same 64K context.
    assert busy["fits"] is True
    assert busy["overrides"]["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
    assert busy["contextLength"] == 65536
    # Never trades the 64K agent floor: 2.5 GB held elsewhere is a refusal.
    blocked = resident_configuration(
        model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl",
        other_used_mib=2500,
    )
    assert blocked["fits"] is False


def test_context_below_the_floor_is_never_reduced_further():
    model = {
        "id": "arch-only", "size_mb": 4000, "vram_required_gb": 5, "context_length": 32768,
        "block_count": 32, "attention_head_count_kv": 8, "embedding_length": 4096,
        "attention_head_count": 32,
    }
    config = resident_configuration(model, total_vram_mb=6144, gpu_platform="linux")
    assert config["fits"] is False
    assert config["contextLength"] == 32768


class TestPlacementLogFilter:
    def test_filtered_lines_parse_identically(self):
        text = _load_log(29, 33, cpu_kv=136) + "".join(
            f"srv  log_server_r: request {index}: POST /v1/chat/completions 200\n" for index in range(50)
        )
        kept = "\n".join(line for line in text.splitlines() if is_placement_log_line(line))
        full = parse_llama_placement(text, expected_model_file="Qwen3.5-9B-Q4_K_M.gguf")
        filtered = parse_llama_placement(kept, expected_model_file="Qwen3.5-9B-Q4_K_M.gguf")
        assert full == filtered
        assert "request" not in kept

    def test_ready_lines_mark_a_finished_load(self):
        assert is_llama_ready_line("main: server is listening on http://0.0.0.0:8080")
        assert is_llama_ready_line("srv  update_slots: all slots are idle")
        assert not is_llama_ready_line("load_tensors: offloaded 33/33 layers to GPU")


def test_moe_fit_overflow_is_partial_even_when_every_layer_is_offloaded():
    # common/fit.cpp can keep a MoE layer "on the GPU" while moving its expert
    # weights to system memory; the load log still says 49/49.
    device = "common_params_fit_impl:   - CUDA0 (NVIDIA RTX PRO 6000): 49 layers (12 overflowing),  90000 MiB used,   1043 MiB free"
    text = _load_log(49, 49, model="/models/qwen3-coder-next-Q4_K_M.gguf")
    text = text.replace("llama_model_loader:", device + "\nllama_model_loader:", 1)
    placement = parse_llama_placement(text, expected_model_file="qwen3-coder-next-Q4_K_M.gguf")
    assert placement["layersOnGpu"] == placement["layersTotal"] == 49
    assert placement["overflowingLayers"] == 12
    assert placement["fullyResident"] is False
    assert placement["status"] == "partial"
    assert "expert weights of 12 layers" in placement["reason"]
    filtered = "\n".join(line for line in text.splitlines() if is_placement_log_line(line))
    assert parse_llama_placement(filtered, expected_model_file="qwen3-coder-next-Q4_K_M.gguf")["status"] == "partial"
