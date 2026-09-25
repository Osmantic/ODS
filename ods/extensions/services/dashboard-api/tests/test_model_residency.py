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
    residency_is_decisive,
    runtime_memory_settings,
)

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "config" / "model-library.json"
# llama-server load logs captured on the fleet (llama.cpp b9014), unedited.
PLACEMENT_LOGS = Path(__file__).resolve().parent / "fixtures" / "llama-placement"

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

    def test_ubatch_alone_keeps_llama_cpps_default_margin(self):
        # 10 GB native Linux, 600 MiB held by a desktop: ubatch 256 is enough,
        # so the 1024 MiB margin (room for other GPU apps) is kept.
        plan = plan_residency_fallback(
            required_mib=6492, available_mib=7400,
            settings={"ubatch": 512, "fitTargetMiB": 1024, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=493, context_length=65536,
        )
        assert plan["changes"] == {"LLAMA_ARG_UBATCH": "256"}
        assert plan["fitTargetMiB"] == 1024

    def test_ubatch_128_comes_before_q4_kv(self):
        # The laptop's shipped profile with ~150 MiB held elsewhere: halving
        # the compute buffer again is enough, the KV cache stays q8_0.
        plan = plan_residency_fallback(
            required_mib=6246, available_mib=6860 - 150,
            settings={"ubatch": 256, "fitTargetMiB": 512, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=246.5, context_length=65536,
            allow_context_reduction=False,
        )
        assert plan["steps"] == ["ubatch 128"]
        assert plan["cacheTypeK"] == "q8_0"

    def test_ubatch_128_is_dropped_when_q4_kv_alone_fits(self):
        # ~400 MiB held elsewhere: q4_0 is needed, and with it ubatch 256
        # still fits, so prompt processing keeps its speed.
        plan = plan_residency_fallback(
            required_mib=6246, available_mib=6860 - 400,
            settings={"ubatch": 256, "fitTargetMiB": 512, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=246.5, context_length=65536,
            allow_context_reduction=False,
        )
        assert plan["steps"] == ["KV cache q4_0"]
        assert "LLAMA_ARG_UBATCH" not in plan["changes"]
        assert plan["ubatch"] == 256
        # ~650 MiB held elsewhere needs both.
        both = plan_residency_fallback(
            required_mib=6246, available_mib=6860 - 650,
            settings={"ubatch": 256, "fitTargetMiB": 512, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=246.5, context_length=65536,
            allow_context_reduction=False,
        )
        assert both["steps"] == ["ubatch 128", "KV cache q4_0"]

    def test_operator_controls_are_never_changed(self):
        plan = plan_residency_fallback(
            required_mib=6492, available_mib=6860,
            settings={"ubatch": 512, "fitTargetMiB": 1536, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=493, context_length=65536,
            allow_context_reduction=False,
            locked_keys=("LLAMA_ARG_FIT_TARGET",),
        )
        assert plan is None or "LLAMA_ARG_FIT_TARGET" not in plan["changes"]
        best = plan_residency_fallback(
            required_mib=6492, available_mib=6860,
            settings={"ubatch": 512, "fitTargetMiB": 1536, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=493, context_length=65536,
            allow_context_reduction=False,
            locked_keys=("LLAMA_ARG_FIT_TARGET", "LLAMA_ARG_UBATCH"), best_effort=True,
        )
        assert best["fits"] is False
        assert best["fitTargetMiB"] == 1536 and best["ubatch"] == 512
        assert best["steps"] == ["KV cache q4_0"]

    def test_nothing_fits_returns_none_or_the_smallest_configuration(self):
        kwargs = dict(
            required_mib=6246, available_mib=6860 - 1200,
            settings={"ubatch": 256, "fitTargetMiB": 512, "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=1088, compute_mib=246.5, context_length=65536,
        )
        assert plan_residency_fallback(**kwargs) is None
        best = plan_residency_fallback(best_effort=True, **kwargs)
        assert best["fits"] is False
        assert best["steps"] == ["ubatch 128", "KV cache q4_0"]
        assert best["contextLength"] == 65536  # never below the agent floor

    def test_every_gpu_keeps_its_own_margin(self):
        # Two GPUs: llama.cpp sums need and free over devices but keeps the
        # fit target free on each one.
        settings = {"ubatch": 512, "fitTargetMiB": 1024, "cacheTypeK": "f16", "cacheTypeV": "f16"}
        single = plan_residency_fallback(
            required_mib=60000, available_mib=62000, settings=settings,
            kv_mib=4000, compute_mib=900, context_length=131072, gpu_count=1,
        )
        assert single["changes"] == {}
        dual = plan_residency_fallback(
            required_mib=60000, available_mib=62000, settings=settings,
            kv_mib=4000, compute_mib=900, context_length=131072, gpu_count=2,
        )
        assert dual["changes"] == {"LLAMA_ARG_UBATCH": "256"}


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

    @pytest.mark.parametrize(
        ("model_name", "cores", "expected"),
        [
            # Performance cores only: every core counts.
            ("12th Gen Intel(R) Core(TM) i5-12400F", 6, 6),
            ("12th Gen Intel(R) Core(TM) i3-12100", 4, 4),
            ("13th Gen Intel(R) Core(TM) i3-13100F", 4, 4),
            # Hybrid parts whose split the OS hides keep the 3/8 estimate.
            ("12th Gen Intel(R) Core(TM) i5-12600K", 10, 3),
            ("12th Gen Intel(R) Core(TM) i5-12450H", 8, 3),
            ("13th Gen Intel(R) Core(TM) i5-13400", 10, 3),
        ],
    )
    def test_performance_only_intel_parts_use_every_core(self, tmp_path, model_name, cores, expected):
        for cpu in range(cores):
            self._cpu(tmp_path, cpu, cpu)
        (tmp_path / "devices" / "system" / "cpu" / "online").write_text(f"0-{cores - 1}\n")
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text(f"processor\t: 0\nmodel name\t: {model_name}\nflags\t\t: fpu sse sse2 avx2\n")
        assert performance_core_count(sysfs_root=str(tmp_path), cpuinfo_path=str(cpuinfo), system="linux") == expected


# Every default the installer picks for an NVIDIA VRAM tier must stay fully
# on the GPU. installers/phases/02-detection.sh runs the selector with the
# 64K Hermes floor and, for the Pixel default, without a size ceiling; that
# is the route below. The models and contexts are the curated picks of
# tests/fixtures/model-selection-golden.json (#6708): residency adds
# settings, never another model or context, on every tier.
#
# Below 8 GB the residency estimate is not calibrated (no 4 or 6 GB card in
# the fleet), so it tunes the settings but does not decide the pick: the
# capacity rule does. On 4 GB the 2B default stays on the GPU at 64K with
# ubatch 256 and a 512 MiB margin (native Linux; WDDM also needs q8_0 KV).
UBATCH_FIT = {"LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512"}
CURATED_DEFAULTS = {
    4096: ("qwen3.5-2b-q4", 65536),
    6144: ("qwen3.5-4b-q4", 65536),
    8151: ("qwen3.5-9b-q4", 65536),
    8192: ("qwen3.5-9b-q4", 65536),
    10240: ("qwen3.5-9b-q4", 65536),
    12282: ("qwen3.5-9b-q4", 65536),
    16376: ("qwen3.5-9b-q4", 65536),
    20480: ("qwen3.5-27b-q4", 65536),
    24564: ("qwen3.5-27b-q4", 65536),
    32607: ("qwen3.5-27b-q4", 65536),
    49140: ("qwen3.6-35b-a3b-ud-q4", 131072),
    97887: ("qwen3-coder-next-q4", 131072),
    195774: ("qwen3-coder-next-q4", 131072),
}
TIER_TABLE = [
    # vram_mb (sum over GPUs), platform, gpu count, model id, context, residency settings
    (4096, "linux", 1, "qwen3.5-2b-q4", 65536, UBATCH_FIT),
    (4096, "wsl", 1, "qwen3.5-2b-q4", 65536, {
        **UBATCH_FIT,
        "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on",
    }),
    # 6GB and 8GB: the runtime profiles themselves carry ubatch 256 / fit target 512.
    (6144, "wsl", 1, "qwen3.5-4b-q4", 65536, {}),
    (6144, "linux", 1, "qwen3.5-4b-q4", 65536, {}),
    (8151, "wsl", 1, "qwen3.5-9b-q4", 65536, {}),
    (8151, "linux", 1, "qwen3.5-9b-q4", 65536, {}),
    (8192, "windows", 1, "qwen3.5-9b-q4", 65536, {}),
    (10240, "linux", 1, "qwen3.5-9b-q4", 65536, {}),
    (12282, "linux", 1, "qwen3.5-9b-q4", 65536, {}),
    (12282, "wsl", 1, "qwen3.5-9b-q4", 65536, {}),
    (16376, "linux", 1, "qwen3.5-9b-q4", 65536, {}),
    # 20GB: the 27B's Q8-KV profile; WDDM withholds 150 MiB more, so ubatch 256.
    (20480, "linux", 1, "qwen3.5-27b-q4", 65536, {}),
    (20480, "wsl", 1, "qwen3.5-27b-q4", 65536, {"LLAMA_ARG_UBATCH": "256"}),
    (24564, "linux", 1, "qwen3.5-27b-q4", 65536, {}),
    (32607, "linux", 1, "qwen3.5-27b-q4", 65536, {}),
    (32607, "wsl", 1, "qwen3.5-27b-q4", 65536, {}),
    (49140, "linux", 1, "qwen3.6-35b-a3b-ud-q4", 131072, {}),
    (97887, "linux", 1, "qwen3-coder-next-q4", 131072, {}),
    (195774, "linux", 2, "qwen3-coder-next-q4", 131072, {}),
]


def _installer_selection(vram_mb, platform, gpu_count, *, agent_ready_only=False, ram_gb=32,
                         other_used_mib=0.0, catalog=None):
    selector = _load_selector()
    catalog = selector.load_catalog(CATALOG) if catalog is None else catalog
    capacity, _ = selector.usable_memory_gb("nvidia", "discrete", vram_mb, ram_gb)
    ranked = selector.rank_models(
        catalog, capacity, "qwen", True, "nvidia", "discrete", vram_mb, ram_gb, "amd64",
        agent_ready_only=agent_ready_only, min_context=65536,
        gpu_platform=platform, gpu_count=gpu_count, other_used_mib=other_used_mib,
    )
    return selector, ranked


@pytest.mark.parametrize(("vram_mb", "platform", "gpu_count", "model_id", "context", "settings"), TIER_TABLE)
def test_nvidia_tier_defaults_are_fully_gpu_resident(vram_mb, platform, gpu_count, model_id, context, settings):
    selector, ranked = _installer_selection(vram_mb, platform, gpu_count)
    selected = ranked[0]
    runtime_profile = selected.get("_runtime_profile")
    assert (selected["id"], selector.effective_context_length(selected, runtime_profile)) == (model_id, context)
    # Residency changes settings, never the tier's model or context.
    assert (model_id, context) == CURATED_DEFAULTS[vram_mb]
    residency = selected["_gpu_residency"]
    assert selected["_residency_overrides"] == settings
    assert selected["_residency_decisive"] is (vram_mb >= 7680 and model_id.startswith("qwen3.5-"))
    assert residency["fits"] is True, residency
    assert residency["headroomMiB"] >= 0
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


def test_4gb_capacity_pick_that_spills_is_kept_and_reported():
    """Residency never changes the pick on GPUs below the calibrated range.

    The estimate predicts that Phi-4 mini spills on a 4 GB card at any
    context, but the platform reserve was fitted on 8 GB and larger cards and
    no 4 GB card has been measured. When the capacity rule picks it (here: an
    installable copy alone in the catalog), it keeps its declared settings;
    activation and the residency watcher verify the placement and report it.
    """
    model = _catalog_by_id()["phi4-mini-q4"]
    assert residency_is_decisive(model, 4096) is False
    assert residency_is_decisive(model, 8151) is True
    selector = _load_selector()
    only = [{**selector.normalize_model(model), "install_recommendation": True}]
    for platform in ("linux", "wsl"):
        config = resident_configuration(
            model, total_vram_mb=4096, context_length=8192, gpu_platform=platform,
        )
        assert config["fits"] is False and config["idleFits"] is False
        selector, ranked = _installer_selection(4096, platform, 1, catalog=only)
        selected = ranked[0]
        assert selected["id"] == "phi4-mini-q4"
        assert selector.effective_context_length(selected, selected.get("_runtime_profile")) == 8192
        assert selected["_residency_spills"] is True
        assert selected["_residency_overrides"] == {}
        reason = selector.recommendation_reason(selected, 4.0, "VRAM", "nvidia", "high")
        assert "does not stay fully on this GPU" in reason
        assert "reported after load" in reason


def test_4gb_default_stays_on_the_gpu_with_the_ladder():
    """The curated 4 GB default (Qwen3.5 2B at 64K) fits with ubatch 256 and a
    512 MiB margin; residency only tunes it (4 GB is uncalibrated)."""
    selector, ranked = _installer_selection(4096, "linux", 1)
    selected = ranked[0]
    assert selected["id"] == "qwen3.5-2b-q4"
    assert selected["_residency_decisive"] is False
    assert selected["_residency_fits"] is True
    assert selected["_residency_overrides"] == UBATCH_FIT
    reason = selector.recommendation_reason(selected, 4.0, "VRAM", "nvidia", "high")
    assert "Stays fully on the GPU" in reason and "ubatch 256" in reason


def test_selector_env_carries_the_residency_settings(tmp_path):
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "select-model.py"),
            "--catalog", str(CATALOG), "--backend", "nvidia", "--memory-type", "discrete",
            "--vram-mb", "4096", "--ram-gb", "16", "--profile", "qwen", "--tier", "1",
            "--host-arch", "amd64", "--installable-only", "--min-context", "65536",
            "--platform", "wsl", "--env",
        ],
        capture_output=True, text=True, check=True,
    )
    env = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert env["GGUF_FILE"] == '"Qwen3.5-2B-Q4_K_M.gguf"'
    assert env["MAX_CONTEXT"] == '"65536"'
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


def test_other_gpu_users_are_budgeted_on_native_linux():
    model = _catalog_by_id()["qwen3.5-9b-q4"]
    profile = next(p for p in model["runtime_profiles"] if p["id"] == "nvidia-8gb-64k-q8-kv")
    idle = resident_configuration(model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="linux")
    busy = resident_configuration(
        model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="linux",
        other_used_mib=400,
    )
    assert idle["fits"] and idle["overrides"] == {} and idle["idleFits"]
    # 400 MiB held elsewhere (a desktop on the dGPU, Whisper): q4_0 KV keeps
    # every layer on the GPU at the same 64K context.
    assert busy["fits"] is True
    assert busy["overrides"]["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
    assert busy["contextLength"] == 65536
    # Never trades the 64K agent floor. With 2.5 GB held elsewhere nothing
    # fits, yet the model itself fits the idle GPU: the smallest allowed
    # configuration is offered for a caller that loads anyway and reports.
    blocked = resident_configuration(
        model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="linux",
        other_used_mib=2500,
    )
    assert blocked["fits"] is False and blocked["idleFits"] is True
    assert blocked["bestEffort"]["steps"] == ["ubatch 128", "KV cache q4_0"]
    assert blocked["bestEffort"]["contextLength"] == 65536
    assert busy["residency"]["vramOversubscribedMiB"] == 0.0


@pytest.mark.parametrize(
    ("held_mib", "oversubscribed_mib"),
    # The fleet check's holders (405 and 1049 MiB, 2026-09-25) and a larger one.
    [(0, 0.0), (405, 0.0), (1049, 5.03), (1500, 456.03)],
)
def test_wsl_other_gpu_users_do_not_shrink_the_plan(held_mib, oversubscribed_mib):
    """Under WSL CUDA offers llama.cpp the same budget beside other processes.

    The laptop logged 6860 MiB free beside 0, 405 and 1049 MiB held by
    another WSL process and loaded the V1f profile 33/33 at full speed. The
    plan stays the idle one; only physical oversubscription (other processes
    + llama.cpp's projection + its fit target against the 7802 MiB the driver
    leaves) is computed, for a report.
    """
    model = _catalog_by_id()["qwen3.5-9b-q4"]
    profile = next(p for p in model["runtime_profiles"] if p["id"] == "nvidia-8gb-64k-q8-kv")
    config = resident_configuration(
        model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform="wsl",
        other_used_mib=held_mib,
    )
    assert config["fits"] is True and config["idleFits"] is True
    assert config["overrides"] == {} and config["steps"] == []
    residency = config["residency"]
    assert residency["otherUsedMiB"] == held_mib
    assert residency["budgetedOtherUsedMiB"] == 0.0
    assert residency["availableMiB"] == LAPTOP_TOTAL_MIB - platform_reserve_mib(LAPTOP_TOTAL_MIB, "wsl")
    assert residency["vramOversubscribedMiB"] == pytest.approx(oversubscribed_mib, abs=0.01)
    # Native Windows and native Linux keep budgeting the same memory.
    for native in ("windows", "linux"):
        busy = resident_configuration(
            model, total_vram_mb=LAPTOP_TOTAL_MIB, runtime_profile=profile, gpu_platform=native,
            other_used_mib=held_mib,
        )
        assert busy["residency"]["budgetedOtherUsedMiB"] == held_mib
        if held_mib >= 405:
            assert "LLAMA_ARG_CACHE_TYPE_K" in (busy["overrides"] or (busy["bestEffort"] or {}).get("overrides", {}))


def test_wsl_installer_keeps_the_profile_and_reports_oversubscription():
    """select-model on the laptop under WSL beside 1500 MiB held elsewhere."""
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "select-model.py"),
            "--catalog", str(CATALOG), "--backend", "nvidia", "--memory-type", "discrete",
            "--vram-mb", str(LAPTOP_TOTAL_MIB), "--ram-gb", "31", "--profile", "qwen", "--tier", "2",
            "--host-arch", "amd64", "--installable-only", "--min-context", "65536",
            "--platform", "wsl", "--other-used-mib", "1500", "--env",
        ],
        capture_output=True, text=True, check=True,
    )
    env = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert env["MODEL_RUNTIME_PROFILE"] == '"nvidia-8gb-64k-q8-kv"'
    assert env["LLAMA_ARG_CACHE_TYPE_K"] == '"q8_0"'
    assert env["LLAMA_ARG_UBATCH"] == '"256"'
    assert env["LLAMA_ARG_FIT_TARGET"] == '"512"'
    assert "oversubscribed by about 456 MiB" in env["MODEL_RECOMMENDATION_REASON"]


@pytest.mark.parametrize("platform", ["windows", "linux"])
@pytest.mark.parametrize("desktop_mib", [300, 600, 900])
def test_8gb_default_absorbs_a_desktop_drawn_on_the_nvidia_gpu(platform, desktop_mib):
    """A desktop on the dGPU (300-900 MiB) never changes the 8 GB default.

    Native Windows and native Linux: the installer measures memory already
    in use and plans the settings around it: ubatch 128 and a q4_0 KV cache
    free up to ~635 MiB at the 64K floor. What still does not fit is loaded
    anyway and reported after load. (Under WSL the desktop does not shrink
    llama.cpp's budget; see test_wsl_other_gpu_users_do_not_shrink_the_plan.)
    """
    selector, ranked = _installer_selection(
        LAPTOP_TOTAL_MIB, platform, 1, other_used_mib=desktop_mib,
    )
    capacity, _ = selector.usable_memory_gb("nvidia", "discrete", LAPTOP_TOTAL_MIB, 32)
    selected = ranked[0]
    assert selected["id"] == "qwen3.5-9b-q4"
    assert selected["_runtime_profile"]["id"] == "nvidia-8gb-64k-q8-kv"
    assert selector.effective_context_length(selected, selected["_runtime_profile"]) == 65536
    assert selected["_residency_idle_fits"] is True
    assert selected["_residency_overrides"]["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
    if selected["_residency_fits"]:
        assert selected["_gpu_residency"]["headroomMiB"] >= 0
    else:
        # Only the largest desktops outgrow what the 64K floor allows.
        assert desktop_mib == 900
        assert selected["_residency_best_effort"] is True
        assert selected["_residency_overrides"]["LLAMA_ARG_UBATCH"] == "128"
        reason = selector.recommendation_reason(selected, capacity, "VRAM", "nvidia", "high")
        assert f"Other processes hold {desktop_mib} MiB" in reason


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


class TestCapturedLoadLogs:
    """Placement parsed from unedited llama.cpp b9014 logs captured on the fleet."""

    def test_laptop_shipped_profile_is_29_of_33(self):
        text = (PLACEMENT_LOGS / "laptop-rtx5070-wsl-b9014-v0-ub512-fitt1024.txt").read_text(encoding="utf-8")
        placement = parse_llama_placement(text, expected_model_file="Qwen3.5-9B-Q4_K_M.gguf")
        assert placement["status"] == "partial"
        assert (placement["layersOnGpu"], placement["layersTotal"]) == (29, 33)
        assert placement["projectedDeviceMiB"] == 6492
        assert placement["freeDeviceMiB"] == 6860
        assert placement["fitTargetMiB"] == 1024
        assert placement["deviceCount"] == 1
        assert placement["cpuKvMiB"] == pytest.approx(136 + 6.28)
        plan = plan_residency_fallback(
            required_mib=placement["projectedDeviceMiB"],
            available_mib=placement["freeDeviceMiB"],
            settings={"ubatch": 512, "fitTargetMiB": placement["fitTargetMiB"], "cacheTypeK": "q8_0", "cacheTypeV": "q8_0"},
            kv_mib=placement["kvMiB"], compute_mib=placement["gpuComputeMiB"], context_length=65536,
        )
        # The benchmarked V1f fix, planned from the captured numbers alone.
        assert plan["changes"] == {"LLAMA_ARG_UBATCH": "256", "LLAMA_ARG_FIT_TARGET": "512"}

    def test_laptop_benchmarked_fix_is_fully_resident(self):
        text = (PLACEMENT_LOGS / "laptop-rtx5070-wsl-b9014-v1f-ub256-fitt512.txt").read_text(encoding="utf-8")
        placement = parse_llama_placement(text, expected_model_file="Qwen3.5-9B-Q4_K_M.gguf")
        assert placement["status"] == "fully_resident"
        assert (placement["layersOnGpu"], placement["layersTotal"]) == (33, 33)
        assert placement["projectedDeviceMiB"] == 6246
        assert placement["fitTargetMiB"] == 512
        assert placement["cpuKvMiB"] == 0

    def test_two_gpu_tower_is_fully_resident_with_a_margin_per_gpu(self):
        text = (PLACEMENT_LOGS / "tower2-2xrtxpro6000-b9014-coder-next.txt").read_text(encoding="utf-8")
        placement = parse_llama_placement(text, expected_model_file="qwen3-coder-next-Q4_K_M.gguf")
        assert placement["status"] == "fully_resident"
        assert (placement["layersOnGpu"], placement["layersTotal"]) == (49, 49)
        assert placement["deviceCount"] == 2
        assert placement["fitTargetMiB"] == 1024
        assert placement["projectedDeviceMiB"] == 51357
        assert placement["freeDeviceMiB"] == 192897
        assert placement["overflowingLayers"] == 0


def _moe_fit_log(device_lines, *, set_lines=()):
    text = _load_log(49, 49, model="/models/qwen3-coder-next-Q4_K_M.gguf")
    block = "\n".join([*set_lines, *device_lines])
    return text.replace("llama_model_loader:", block + "\nllama_model_loader:", 1)


def test_moe_overflow_to_the_next_gpu_stays_on_the_gpu():
    """common/fit.cpp can split one MoE layer across two GPUs.

    Its first overflowing layer then goes to the next device, not to system
    memory (``set ngl_per_device[0].(n_layer, n_part, overflow_type)=(.., ..,
    UP)``); only overflow beyond that, or on the last GPU, is on the CPU.
    """
    split = _moe_fit_log(
        [
            "common_params_fit_impl:   - CUDA0 (NVIDIA RTX PRO 6000): 25 layers ( 1 overflowing),  96000 MiB used,   1040 MiB free",
            "common_params_fit_impl:   - CUDA1 (NVIDIA RTX PRO 6000): 24 layers ( 0 overflowing),  60000 MiB used,  36000 MiB free",
        ],
        set_lines=(
            "common_params_fit_impl: set ngl_per_device[0].(n_layer, n_part, overflow_type)=(25,  1, UP), id_dense_start=1",
        ),
    )
    placement = parse_llama_placement(split, expected_model_file="qwen3-coder-next-Q4_K_M.gguf")
    assert placement["status"] == "fully_resident"
    assert placement["overflowingLayers"] == 0
    assert placement["nextGpuOverflowLayers"] == 1
    assert placement["deviceCount"] == 2
    filtered = "\n".join(line for line in split.splitlines() if is_placement_log_line(line))
    assert parse_llama_placement(filtered, expected_model_file="qwen3-coder-next-Q4_K_M.gguf")["status"] == "fully_resident"
    # More overflow than the one split layer, or overflow on the last GPU,
    # puts expert weights in system memory.
    spilled = _moe_fit_log(
        [
            "common_params_fit_impl:   - CUDA0 (NVIDIA RTX PRO 6000): 25 layers ( 3 overflowing),  96000 MiB used,   1040 MiB free",
            "common_params_fit_impl:   - CUDA1 (NVIDIA RTX PRO 6000): 24 layers ( 2 overflowing),  96000 MiB used,   1030 MiB free",
        ],
        set_lines=(
            "common_params_fit_impl: set ngl_per_device[0].(n_layer, n_part, overflow_type)=(25,  3, GATE), id_dense_start=1",
            "common_params_fit_impl: set ngl_per_device[1].(n_layer, n_part, overflow_type)=(24,  2, UP), id_dense_start=1",
        ),
    )
    placement = parse_llama_placement(spilled, expected_model_file="qwen3-coder-next-Q4_K_M.gguf")
    assert placement["status"] == "partial"
    assert placement["overflowingLayers"] == 2 + 2
    assert placement["nextGpuOverflowLayers"] == 1


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
