"""scripts/select-model.py --llm-runtime vllm, and proof that GGUF picks are unchanged.

vLLM catalog entries (``"runtime": "vllm"``) are eligible only when the
selector is told the runtime is vllm and a GPU named by --gpu has one of the
entry's compute capabilities and at least its min_gpu_mib. Without
--llm-runtime vllm the selector's output over every installer envelope
(tests/fixtures/model-selection-envelopes.json) is byte-identical to the
output for the same catalog without its vLLM entries.
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import io
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"
SELECTOR = ROOT / "scripts" / "select-model.py"
ENVELOPES = ROOT / "tests" / "fixtures" / "model-selection-envelopes.json"
TIER_MAP = ROOT / "installers" / "lib" / "tier-map.sh"
SIMULATOR = ROOT / "scripts" / "simulate-model-selection.py"

# nvidia-smi --query-gpu=uuid,compute_cap,memory.total --format=csv,noheader,nounits rows.
# GB300 and RTX PRO 6000 memory: the DGX Station Workstation06 (2026-10-27).
GB300 = "GPU-test-gb300, 10.3, 256703"
RTX_PRO_6000 = "GPU-test-rtx-pro-6000, 12.0, 97887"
RTX_5090 = "GPU-test-rtx-5090, 12.0, 32607"
B200 = "GPU-test-b200, 10.0, 183359"
H200 = "GPU-test-h200, 9.0, 143771"
# The installer's selector arguments on the DGX Station, plus the vLLM runtime.
VLLM = ("--backend", "nvidia", "--memory-type", "discrete", "--vram-mb", "354590", "--ram-gb", "494",
        "--profile", "qwen", "--tier", "NV_ULTRA", "--max-size-mb", "0", "--host-arch", "arm64",
        "--installable-only", "--min-context", "65536", "--llm-runtime", "vllm")
VLLM_ENV_KEYS = {
    "LLM_MODEL", "GGUF_FILE", "GGUF_URL", "GGUF_SHA256", "MAX_CONTEXT", "LLM_MODEL_SIZE_MB",
    "MODEL_RECOMMENDATION_SOURCE", "MODEL_RECOMMENDATION_POLICY", "MODEL_RECOMMENDATION_CONFIDENCE",
    "MODEL_RECOMMENDATION_REASON", "MODEL_RECOMMENDED_ALTERNATIVES", "PIXEL_AGENT_MODEL_READY",
    "VLLM_MODEL_DIR", "VLLM_SERVED_MODEL", "VLLM_ARGS", "VLLM_GPU_UUID", "VLLM_GPU_MEMORY_UTILIZATION",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _selector():
    return _load("ods_select_model_vllm_test", SELECTOR)


def select(*args: str, catalog: Path = CATALOG) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = _selector().main(["--catalog", str(catalog), *args])
    return status, out.getvalue(), err.getvalue()


def env_values(stdout: str) -> dict[str, str]:
    """Decode --env lines the way lib/safe-env.sh load_env_from_output does."""
    values = {}
    for line in stdout.splitlines():
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)="(.*)"', line)
        assert match, line
        values[match.group(1)] = (
            match.group(2).replace('\\"', '"').replace("\\$", "$").replace("\\`", "`").replace("\\\\", "\\")
        )
    return values


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _vllm_entries() -> list[dict]:
    return [model for model in _catalog()["models"] if model.get("runtime") == "vllm"]


def _fits(capability: str, memory_mib: int) -> list[dict]:
    return [model for model in _vllm_entries()
            if capability in model["compute_capability"] and model["min_gpu_mib"] <= memory_mib]


def _best_fitting(capability: str, memory_mib: int) -> tuple[dict, list[dict]]:
    fitting = _fits(capability, memory_mib)
    assert fitting
    return max(fitting, key=lambda model: model["selection"]["vllm"]), fitting


def _gpu_args(*rows: str) -> list[str]:
    return [arg for row in rows for arg in ("--gpu", row)]


def test_dgx_station_gets_the_top_priority_vllm_model_on_its_gb300():
    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000, GB300), "--env")

    assert status == 0, err
    env = env_values(out)
    best, fitting = _best_fitting("10.3", 256703)
    assert set(env) == VLLM_ENV_KEYS
    assert env["LLM_MODEL"] == best["llm_model_name"]
    assert env["VLLM_GPU_UUID"] == "GPU-test-gb300"
    assert env["VLLM_MODEL_DIR"] == f"{best['hf_repo'].replace('/', '--')}@{best['revision'][:8]}"
    assert env["VLLM_SERVED_MODEL"] == best["served_model_name"]
    assert shlex.split(env["VLLM_ARGS"]) == best["vllm_args"]
    assert env["MAX_CONTEXT"] == str(best["context_length"]) == "131072"
    assert env["LLM_MODEL_SIZE_MB"] == str(best["size_mb"])
    assert env["GGUF_FILE"] == env["GGUF_URL"] == env["GGUF_SHA256"] == ""
    pixel = ((best.get("app_compatibility") or {}).get("pixel_agent") or {}).get("status")
    assert env["PIXEL_AGENT_MODEL_READY"] == ("true" if pixel == "verified" else "false")
    assert env["MODEL_RECOMMENDATION_POLICY"] == "vllm-gpu-fit-v1"
    assert env["MODEL_RECOMMENDATION_SOURCE"] == "catalog_vllm_pre_download"
    # An entry without its own share still sends vLLM's 0.9, so a newly
    # picked model never inherits an earlier model's .env share.
    assert "gpu_memory_utilization" not in best and env["VLLM_GPU_MEMORY_UTILIZATION"] == "0.9"
    assert "GPU-test-gb300 (compute capability 10.3, 256703 MiB)" in env["MODEL_RECOMMENDATION_REASON"]
    # The alternatives are every entry that fits either GPU (the RTX PRO 6000's own one too).
    alternatives = [item.split(":")[0] for item in env["MODEL_RECOMMENDED_ALTERNATIVES"].split(";")]
    either = {model["id"] for model in fitting + _fits("12.0", 97887)}
    assert alternatives[0] == best["id"] and set(alternatives) == either


def test_the_gb300_fits_the_datacenter_entries_and_the_rtx_pro_6000_its_own():
    _, fitting = _best_fitting("10.3", 256703)
    assert {model["id"] for model in fitting} == {"qwen3.8-flash-next-nvfp4-vllm", "glm-5.3-flash-nvfp4-vllm"}
    assert [model["id"] for model in _fits("12.0", 97887)] == ["qwen3.6-35b-a3b-nvfp4-vllm"]
    assert _fits("12.0", 32607) == []


def test_a_lone_rtx_pro_6000_gets_its_class_entry_at_half_the_card():
    # A workstation's only GPU also runs ComfyUI and voice, so this entry
    # takes half of it; the selector hands that share to the installer.
    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000), "--env")

    assert status == 0, err
    env = env_values(out)
    best, _ = _best_fitting("12.0", 97887)
    assert set(env) == VLLM_ENV_KEYS
    assert env["LLM_MODEL"] == best["llm_model_name"] == "qwen3.6-35b-a3b-nvfp4"
    assert env["VLLM_GPU_UUID"] == "GPU-test-rtx-pro-6000"
    assert env["VLLM_GPU_MEMORY_UTILIZATION"] == "0.5" == f"{best['gpu_memory_utilization']:g}"
    assert shlex.split(env["VLLM_ARGS"]) == best["vllm_args"]
    assert "--gpu-memory-utilization" not in env["VLLM_ARGS"]
    reason = env["MODEL_RECOMMENDATION_REASON"]
    # 97887 MiB * 0.5 / 1024 - 21.85 GiB of weights.
    assert "leave about 25.9 GiB of vLLM's 0.50 share of that GPU" in reason, reason


def test_a_b200_gets_only_what_fits_its_memory():
    status, out, err = select(*VLLM, *_gpu_args(B200), "--env")

    assert status == 0, err
    env = env_values(out)
    best, fitting = _best_fitting("10.0", 183359)
    assert env["LLM_MODEL"] == best["llm_model_name"]
    assert "glm-5.3-flash-nvfp4-vllm" not in {model["id"] for model in fitting}
    assert "glm-5.3-flash-nvfp4-vllm" not in env["MODEL_RECOMMENDED_ALTERNATIVES"]


@pytest.mark.parametrize("rows,expected", [
    ((RTX_5090,), "needs one GPU with compute capability 10.0 or 10.3 and at least"),
    ((RTX_5090,), "needs one GPU with compute capability 12.0 and at least 51200 MiB"),
    ((H200,), "GPU-test-h200 (compute capability 9.0, 143771 MiB)"),
    ((), "pass each one as --gpu"),
    (("GPU-test-gb10, 12.1, [N/A]",), "is not UUID,COMPUTE_CAP,MEMORY_MIB"),
])
def test_no_fitting_gpu_fails_instead_of_falling_back(rows, expected):
    status, out, err = select(*VLLM, *_gpu_args(*rows), "--env")

    assert status == 2
    assert out == ""
    assert err.startswith("error: ") and expected in err


def test_the_exit_status_reaches_the_installer():
    result = subprocess.run(
        [sys.executable, str(SELECTOR), "--catalog", str(CATALOG), *VLLM, *_gpu_args(RTX_5090), "--env"],
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode, result.stdout) == (2, "")
    assert "no vLLM catalog model fits this host" in result.stderr


def test_vllm_needs_an_nvidia_backend():
    status, out, err = select("--backend", "amd", "--llm-runtime", "vllm", *_gpu_args(GB300), "--env")
    assert (status, out) == (2, "")
    assert "need NVIDIA GPUs" in err


def test_check_fit_is_refused_for_vllm():
    status, out, err = select(*VLLM, *_gpu_args(GB300), "--check-fit",
                              "--model-id", "glm-5.3-flash-nvfp4-vllm", "--context", "65536")
    assert (status, out) == (1, "")
    assert "--check-fit checks llama.cpp models" in err


def test_context_floor_is_a_preference_unless_required():
    status, out, err = select(*VLLM, *_gpu_args(GB300), "--min-context", "262144", "--env")
    assert status == 0, err
    assert "No vLLM catalog model serves the 262144 context floor" in env_values(out)["MODEL_RECOMMENDATION_REASON"]

    status, out, err = select(*VLLM, *_gpu_args(GB300), "--min-context", "262144", "--require-min-context", "--env")
    assert (status, out) == (2, "")
    assert "at the 262144 context floor" in err


def test_the_largest_fitting_gpu_is_the_one_vllm_owns():
    status, out, err = select(*VLLM, *_gpu_args(GB300, "GPU-test-b300, 10.3, 275040", GB300), "--env")
    assert status == 0, err
    assert env_values(out)["VLLM_GPU_UUID"] == "GPU-test-b300"


# A fresh install adds nvidia-smi memory.free as a fourth field. vLLM refuses
# to start unless its share of the card is free once its CUDA context exists:
# half an RTX PRO 6000 is ceil(0.5 * 97887) = 48944 MiB, plus START_MARGIN_MIB
# (1024) makes 49968 MiB free.
def test_a_gpu_without_room_for_vllm_is_not_given_to_vllm():
    # tower2 (2026-10-27): another model server held about 84 GB of each card.
    rows = ("GPU-test-busy-0, 12.0, 97887, 13278", "GPU-test-busy-1, 12.0, 97887, 13298")
    status, out, err = select(*VLLM, *_gpu_args(*rows), "--env")

    assert (status, out) == (4, "")
    assert err == (
        "error: other programs use the GPU memory the vLLM engine needs: Qwen 3.6 35B A3B NVFP4 (vLLM) "
        "needs 49968 MiB free when it starts (0.50 of the GPU plus 1024 MiB for its CUDA context), "
        "and the freest GPU it fits, GPU-test-busy-1, has 13298 MiB free\n"
    )


def test_vllm_takes_a_gpu_with_room_to_start_and_says_why():
    status, out, err = select(*VLLM, *_gpu_args("GPU-test-busy, 12.0, 97887, 13278",
                                                "GPU-test-exact, 12.0, 97887, 49968"), "--env")
    assert status == 0, err
    env = env_values(out)
    assert env["VLLM_GPU_UUID"] == "GPU-test-exact"
    assert env["MODEL_RECOMMENDATION_REASON"].endswith(
        " Qwen 3.6 35B A3B NVFP4 (vLLM) would run on GPU GPU-test-busy, but other programs use the memory "
        "it needs there.")

    status, _, err = select(*VLLM, *_gpu_args("GPU-test-short, 12.0, 97887, 49967"), "--env")
    assert status == 4 and "has 49967 MiB free" in err


def test_a_busy_gb300_gives_vllm_the_idle_rtx_pro_6000_and_says_why():
    # A DGX Station whose GB300 runs another model server: the RTX PRO 6000's
    # own entry runs instead of the GB300's top pick, and the reason says so.
    best_gb300, _ = _best_fitting("10.3", 256703)
    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000 + ", 97000", GB300 + ", 30000"), "--env")

    assert status == 0, err
    env = env_values(out)
    assert (env["VLLM_GPU_UUID"], env["LLM_MODEL"]) == ("GPU-test-rtx-pro-6000", "qwen3.6-35b-a3b-nvfp4")
    assert (f" {best_gb300['name']} would run on GPU GPU-test-gb300, but other programs use the memory it "
            f"needs there.") in env["MODEL_RECOMMENDATION_REASON"]

    # Idle, the GB300 keeps its top pick and the reason no note.
    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000 + ", 97000", GB300 + ", 255000"), "--env")
    assert status == 0, err
    env = env_values(out)
    assert (env["VLLM_GPU_UUID"], env["LLM_MODEL"]) == ("GPU-test-gb300", best_gb300["llm_model_name"])
    assert "other programs" not in env["MODEL_RECOMMENDATION_REASON"]

    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000 + ", 97000"))
    assert status == 0, err
    assert json.loads(out)["gpu"] == {"uuid": "GPU-test-rtx-pro-6000", "compute_capability": "12.0",
                                      "memory_mib": 97887, "free_mib": 97000}


def test_equal_gpus_go_to_the_one_with_more_memory_free():
    rows = ("GPU-test-desktop, 12.0, 97887, 95000", "GPU-test-idle, 12.0, 97887, 97000")
    status, out, err = select(*VLLM, *_gpu_args(*rows), "--env")
    assert status == 0, err
    assert env_values(out)["VLLM_GPU_UUID"] == "GPU-test-idle"


def test_free_memory_never_lets_a_gpu_fit_that_its_hardware_does_not():
    # A free H200 still lacks a compute capability any entry names: no fit, not busy.
    status, out, err = select(*VLLM, *_gpu_args(H200 + ", 143000"), "--env")
    assert (status, out) == (2, "")
    assert "GPU-test-h200 (compute capability 9.0, 143771 MiB, 143000 MiB free)" in err


def test_an_unreadable_free_field_leaves_that_gpu_unknown():
    # [N/A] on one GPU must not take vLLM from the whole host.
    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000 + ", [N/A]", GB300 + ", 250000"), "--env")
    assert status == 0, err
    assert env_values(out)["VLLM_GPU_UUID"] == "GPU-test-gb300"

    status, out, err = select(*VLLM, *_gpu_args(RTX_PRO_6000 + ", [N/A]"), "--env")
    assert status == 0, err
    assert env_values(out)["VLLM_GPU_UUID"] == "GPU-test-rtx-pro-6000"

    # The other fields stay required.
    status, out, err = select(*VLLM, *_gpu_args("GPU-test-x, 12.0, [N/A], 1000"), "--env")
    assert (status, out) == (2, "")
    assert "is not UUID,COMPUTE_CAP,MEMORY_MIB[,FREE_MIB]" in err


def test_vllms_default_share_is_the_memory_estimates():
    selector = _selector()
    assert selector.memory_share({}) == selector.vllm_memory_share({}) == 0.9
    assert selector.memory_share({"gpu_memory_utilization": 0.5}) \
        == selector.vllm_memory_share({"gpu_memory_utilization": 0.5}) == 0.5


def test_json_payload_names_the_gpu_folder_and_estimate():
    status, out, err = select(*VLLM, *_gpu_args(GB300))

    assert status == 0, err
    payload = json.loads(out)
    best, _ = _best_fitting("10.3", 256703)
    assert payload["runtime"] == "vllm"
    assert payload["gpu"] == {"uuid": "GPU-test-gb300", "compute_capability": "10.3", "memory_mib": 256703}
    assert payload["selected"]["id"] == best["id"]
    assert payload["selected"]["file_count"] == len(best["files"])
    assert "files" not in payload["selected"]
    assert payload["selected"]["estimate"]["method"] == "vllm"


def _write_catalog(tmp_path: Path, catalog: dict) -> Path:
    path = tmp_path / "model-library.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def test_selection_priority_decides_between_fitting_entries(tmp_path):
    catalog = _catalog()
    for model in catalog["models"]:
        if model.get("runtime") == "vllm":
            model["selection"]["vllm"] = 100 if model["id"].startswith("glm") else 90
    path = _write_catalog(tmp_path, catalog)

    status, out, err = select(*VLLM, *_gpu_args(GB300), "--env", catalog=path)
    assert status == 0, err
    assert env_values(out)["LLM_MODEL"] == "glm-5.3-flash-nvfp4"
    status, out, err = select(*VLLM, *_gpu_args(B200), "--env", catalog=path)
    assert env_values(out)["LLM_MODEL"] == "qwen3.8-flash-next-nvfp4"

    for model in catalog["models"]:
        if model.get("runtime") == "vllm":
            model["selection"]["vllm"] = 0
    path = _write_catalog(tmp_path, catalog)
    assert select(*VLLM, *_gpu_args(GB300), "--env", catalog=path)[:2] == (2, "")


def test_install_recommendation_false_keeps_an_entry_out_of_installer_picks(tmp_path):
    catalog = _catalog()
    for model in catalog["models"]:
        if model.get("runtime") == "vllm":
            model["install_recommendation"] = False
    path = _write_catalog(tmp_path, catalog)
    assert select(*VLLM, *_gpu_args(GB300), "--env", catalog=path)[:2] == (2, "")


def test_llama_server_selection_never_returns_a_vllm_entry_on_a_gb300_sized_gpu():
    status, out, err = select("--backend", "nvidia", "--memory-type", "discrete", "--vram-mb", "256703",
                              "--ram-gb", "494", "--tier", "NV_ULTRA", "--host-arch", "arm64",
                              "--installable-only", "--min-context", "65536", *_gpu_args(GB300), "--env")
    assert status == 0, err
    env = env_values(out)
    assert env["GGUF_FILE"].endswith(".gguf")
    assert not any(key.startswith("VLLM_") for key in env)
    assert env["LLM_MODEL"] not in {model["llm_model_name"] for model in _vllm_entries()}


def _envelope_args(envelope: dict, simulator) -> list[str]:
    """The arguments scripts/simulate-model-selection.py passes, as the installers do."""
    ceiling = envelope.get("ceiling")
    ceiling = (simulator.tier_map_size_mb(TIER_MAP, str(envelope["tier"]), str(envelope["host_arch"]))
               if ceiling == "tier-map" else int(ceiling or 0))
    return [
        "--backend", envelope["backend"], "--memory-type", envelope["memory_type"],
        "--vram-mb", str(envelope["vram_mb"]), "--ram-gb", str(envelope["ram_gb"]),
        "--profile", "qwen", "--tier", str(envelope["tier"]), "--max-size-mb", str(ceiling),
        "--host-arch", envelope["host_arch"], "--installable-only", "--min-context", "65536",
    ]


def test_gguf_selection_is_byte_identical_with_or_without_vllm_entries(tmp_path):
    catalog = _catalog()
    assert len(_vllm_entries()) == 3
    without = _write_catalog(tmp_path, {
        **catalog, "models": [model for model in catalog["models"] if model.get("runtime") != "vllm"],
    })
    simulator = _load("ods_simulate_model_selection_vllm_test", SIMULATOR)
    envelopes = json.loads(ENVELOPES.read_text(encoding="utf-8"))["envelopes"]
    compared = picked = 0
    for envelope in envelopes:
        args = _envelope_args(envelope, simulator)
        for extra in ([], ["--env"], ["--agent-ready-only", "--env"]):
            today = select(*args, *extra, catalog=without)
            case = (envelope["id"], extra)
            assert select(*args, *extra) == today, case
            assert select(*args, *extra, "--llm-runtime", "llama-server", *_gpu_args(GB300)) == today, case
            compared += 1
            picked += today[0] == 0 and bool(today[1])
    assert compared == 3 * len(envelopes) == 195
    # Not vacuous: every envelope picks a model (the Pixel-verdict route some).
    assert picked >= 2 * len(envelopes)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
