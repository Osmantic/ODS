#!/usr/bin/env python3
"""GPU residency contracts for scripts/llama_gpu_residency.py.

The fixtures are llama-server load logs captured from fleet hosts
(tests/fixtures/llama-placement/README.md lists where each came from).

Run: python3 tests/test-llama-gpu-residency.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "llama_gpu_residency.py"
FIXTURES = ROOT / "tests" / "fixtures" / "llama-placement"
LAPTOP_PARTIAL = FIXTURES / "laptop-rtx5070-9b-64k-partial-b9014.txt"
LAPTOP_RESIDENT = FIXTURES / "laptop-rtx5070-9b-64k-fit512-resident-b9014.txt"
MAC_RESIDENT = FIXTURES / "mac-mini-m4-9b-metal-resident-b8210.txt"
TOWER_RESIDENT = FIXTURES / "tower-rtx5090-27b-32k-resident-docker-timestamps-b9014.txt"
TOWER2_RESIDENT = FIXTURES / "tower2-2xrtxpro6000-coder-next-128k-resident-docker-timestamps-b9014.txt"
# Complete loads (start line, placement, "model loaded") on one RTX PRO 6000.
B9014_RESIDENT = FIXTURES / "tower2-rtxpro6000-qwen35-2b-resident-b9014.txt"
B11146_LV4_RESIDENT = FIXTURES / "tower2-rtxpro6000-qwen35-2b-resident-lv4-b11146.txt"
B11146_DEFAULT_VERBOSITY = FIXTURES / "tower2-rtxpro6000-qwen35-2b-default-verbosity-b11146.txt"

spec = importlib.util.spec_from_file_location("llama_gpu_residency", SCRIPT)
residency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(residency)


def verdict(log_text: str, args: list[str] | None = None, env: dict[str, str] | None = None,
            server_ready: bool = False) -> dict:
    return residency.judge(log_text, args or [], env or {}, "test", server_ready)


class ParsePlacement(unittest.TestCase):
    def test_laptop_default_profile_is_partial(self):
        # The 8 GB default (9B, 64K, q8_0 KV) as shipped: llama.cpp keeps its
        # 1024 MiB margin and moves 4 layers to the CPU.
        result = verdict(LAPTOP_PARTIAL.read_text())
        self.assertEqual(result["status"], "fail")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (29, 33))
        self.assertEqual(result["cpu_model_buffer_mib"], 1078.96)
        self.assertEqual(result["gpu_model_buffer_mib"], 4327.95)
        self.assertEqual(result["cpu_kv_buffer_mib"], 136.0)
        self.assertEqual(result["gpu_kv_buffer_mib"], 952.0)
        self.assertEqual(
            result["fit"],
            {"projected_mib": 6492, "free_mib": 6860, "target_mib": 1024, "shortfall_mib": 656},
        )
        self.assertEqual((result["ubatch"], result["context_size"], result["gpu_compute_buffer_mib"]),
                         (512, 65536, 493.0))
        self.assertEqual(result["llama_build"], 9014)
        self.assertIn("29/33 layers on GPU", result["message"])

    def test_laptop_fix_is_the_fit_margin_not_closing_programs(self):
        # The GPU was idle: 6492 MiB needed, 6860 MiB free. Only llama.cpp's
        # 1024 MiB margin pushed layers out, so freeing VRAM cannot help.
        result = verdict(LAPTOP_PARTIAL.read_text())
        self.assertEqual(result["remedy"], "refit")
        hint = result["fix_hint"]
        self.assertIn("The GPU had room for the whole model", hint)
        self.assertIn("keeps 1024 MiB free as a safety margin and moved 4 layers to the CPU", hint)
        self.assertIn("Closing other programs will not change this", hint)
        self.assertIn("Reload on GPU", hint)
        self.assertIn("LLAMA_ARG_FIT_TARGET=512 and LLAMA_ARG_UBATCH=256", hint)
        self.assertNotIn("close them", hint)
        self.assertNotIn("smaller model", hint)

    def test_laptop_with_smaller_margin_is_resident(self):
        result = verdict(LAPTOP_RESIDENT.read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (33, 33))
        # Token embeddings stay in host memory on a full offload.
        self.assertEqual(result["cpu_model_buffer_mib"], 545.62)
        self.assertEqual(result["cpu_kv_buffer_mib"], 0.0)
        self.assertEqual(result["fit"], {"projected_mib": 6246, "free_mib": 6860, "target_mib": 512})
        self.assertEqual(result["ubatch"], 256)
        self.assertEqual(result["fix_hint"], "")

    def test_metal_b8210_log_is_resident(self):
        result = verdict(MAC_RESIDENT.read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (33, 33))
        self.assertEqual(result["gpu_model_buffer_mib"], 5406.91)
        self.assertEqual(result["gpu_kv_buffer_mib"], 2048.0)
        self.assertEqual(result["fit"], {"projected_mib": 7452, "free_mib": 12073, "target_mib": 1024})
        self.assertEqual(result["llama_build"], 8210)

    def test_docker_timestamped_log_is_resident(self):
        result = verdict(TOWER_RESIDENT.read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (65, 65))
        self.assertEqual(result["cpu_model_buffer_mib"], 682.03)

    def test_two_gpu_split_sums_both_devices(self):
        result = verdict(TOWER2_RESIDENT.read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (49, 49))
        self.assertAlmostEqual(result["gpu_model_buffer_mib"], 23823.69 + 22283.89, places=2)
        self.assertEqual(result["gpu_kv_buffer_mib"], 3072.0)

    def test_newest_run_in_an_appended_log_wins(self):
        partial, resident = LAPTOP_PARTIAL.read_text(), LAPTOP_RESIDENT.read_text()
        self.assertEqual(verdict(partial + "\n" + resident)["status"], "pass")
        self.assertEqual(verdict(resident + "\n" + partial)["status"], "fail")

    def test_draft_model_load_does_not_replace_the_main_model(self):
        draft = textwrap.dedent("""\
            load_tensors: offloaded 5/25 layers to GPU
            load_tensors:   CPU_Mapped model buffer size =   300.00 MiB
        """)
        result = verdict(LAPTOP_RESIDENT.read_text() + "\n" + draft)
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (33, 33))
        self.assertEqual(result["cpu_model_buffer_mib"], 545.62)

    def test_complete_b9014_load_is_resident(self):
        result = verdict(B9014_RESIDENT.read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (25, 25))
        self.assertEqual((result["llama_build"], result["log_verbosity"]), (9014, None))

    def test_b11146_at_verbosity_4_is_resident(self):
        result = verdict(B11146_LV4_RESIDENT.read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (25, 25))
        self.assertEqual((result["llama_build"], result["log_verbosity"]), (11146, 4))
        self.assertEqual(result["fit"], {"projected_mib": 2106, "free_mib": 68971, "target_mib": 1024})

    def test_pinned_host_model_buffer_counts_as_system_ram(self):
        log = textwrap.dedent("""\
            load_tensors: offloaded 20/33 layers to GPU
            load_tensors:    CUDA_Host model buffer size =  1500.00 MiB
            load_tensors:        CUDA0 model buffer size =  3000.00 MiB
        """)
        self.assertEqual(verdict(log)["cpu_model_buffer_mib"], 1500.0)


def without_placement(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "offloaded" not in line)


# llama-server's request log once the model is serving (b9014 wording).
SERVING_ONLY = textwrap.dedent("""\
    srv  update_slots: all slots are idle
    slot launch_slot_: id  0 | task 7 | processing task, is_child = 0
    slot      release: id  0 | task 7 | stop processing: n_tokens = 28, truncated = 0
""")


class UnreadablePlacement(unittest.TestCase):
    """A loaded ODS-managed GPU model whose placement the log does not state fails."""

    def test_b11146_at_default_verbosity_fails_unverified(self):
        # From b9151 llama.cpp logs placement only at verbosity 4: this load
        # went fine, but nothing says where the layers are.
        result = verdict(B11146_DEFAULT_VERBOSITY.read_text())
        self.assertEqual(result["status"], "unverified")
        self.assertNotIn("layers_on_gpu", result)
        self.assertEqual(result["log_verbosity"], 3)
        self.assertIn("logged at verbosity 3", result["message"])
        self.assertIn("LLAMA_ARG_LOG_VERBOSITY", result["fix_hint"])

    def test_loaded_model_without_placement_line_fails_unverified(self):
        stripped = without_placement(B9014_RESIDENT.read_text())
        result = verdict(stripped)
        self.assertEqual(result["status"], "unverified")
        self.assertIn("b9014 printed no 'offloaded N/M layers to GPU' line", result["message"])
        self.assertIn("image ODS pins", result["fix_hint"])

    def test_server_answering_health_counts_as_loaded(self):
        # The laptop excerpt ends before "model loaded"; /health settles it.
        stripped = without_placement(LAPTOP_PARTIAL.read_text())
        self.assertEqual(verdict(stripped)["status"], "unknown")
        self.assertEqual(verdict(stripped, server_ready=True)["status"], "unverified")

    def test_quiet_build_reading_the_old_env_name_is_named(self):
        log = "\n".join([
            "0.00.1 I common_params_print_info: build 9200 (abc1234) with GNU 14.2.0 for Linux x86_64",
            "0.00.2 I log_info: verbosity = 3 (adjust with the `-lv N` CLI arg)",
            "0.00.3 I srv    load_model: loading model '/models/m.gguf'",
            "0.01.0 I srv  llama_server: model loaded",
        ])
        result = verdict(log)
        self.assertEqual(result["status"], "unverified")
        self.assertIn("reads LLAMA_LOG_VERBOSITY", result["fix_hint"])

    def test_unrecognized_load_start_with_a_ready_line_fails(self):
        # "model loaded" follows the load section, so this is not rotation:
        # the build's start line is one ODS does not know.
        result = verdict("srv  llama_server: model loaded\n" + SERVING_ONLY, server_ready=True)
        self.assertEqual(result["status"], "unverified")
        self.assertIn("no model-load section that ODS recognizes", result["message"])

    def test_rotated_out_load_section_is_unknown_not_fail(self):
        result = verdict(SERVING_ONLY, server_ready=True)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("rotated out", result["message"])
        self.assertIn("ods restart llama-server", result["fix_hint"])

    def test_load_in_progress_is_unknown(self):
        partial_load = "\n".join(B9014_RESIDENT.read_text().splitlines()[:20])
        result = verdict(partial_load)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("has not finished loading", result["message"])


class OffloadDeclarations(unittest.TestCase):
    DECLARATIONS = (
        (["--n-cpu-moe", "12"], {}),
        (["-ncmoe=4"], {}),
        (["--cpu-moe"], {}),
        (["-ot", r"blk\..*_exps\.=CPU"], {}),
        ([], {"LLAMA_ARG_N_CPU_MOE": "20"}),
        ([], {"LLAMA_ARG_CPU_MOE": "1"}),
        ([], {"LLAMA_ARG_OVERRIDE_TENSOR": "exps=CPU"}),
    )

    def test_declared_moe_offload_with_every_layer_on_gpu_is_intentional(self):
        resident = TOWER2_RESIDENT.read_text()
        for args, env in self.DECLARATIONS:
            with self.subTest(args=args, env=env):
                result = verdict(resident, args, env)
                self.assertEqual(result["status"], "intentional")
                self.assertTrue(result["intentional_offload"])
                self.assertTrue(result["offload_declarations"])

    def test_declared_moe_offload_never_excuses_layers_on_the_cpu(self):
        # --n-cpu-moe moves expert weights, not layers; a layer spill on top
        # of it is still the silent slowdown this check exists for.
        partial = LAPTOP_PARTIAL.read_text()
        for args, env in self.DECLARATIONS:
            with self.subTest(args=args, env=env):
                result = verdict(partial, args, env)
                self.assertEqual(result["status"], "fail")
                self.assertTrue(result["intentional_offload"])

    def test_empty_or_gpu_only_overrides_are_not_declarations(self):
        resident = TOWER2_RESIDENT.read_text()
        for args, env in (
            ([], {"LLAMA_ARG_N_CPU_MOE": "0"}),
            ([], {"LLAMA_ARG_N_CPU_MOE": ""}),
            ([], {"LLAMA_ARG_CPU_MOE": "0"}),
            (["-ot", r"blk\.0\.=CUDA1"], {}),
            (["--n-cpu-moe", "0"], {}),
        ):
            with self.subTest(args=args, env=env):
                result = verdict(resident, args, env)
                self.assertEqual(result["status"], "pass")
                self.assertFalse(result["intentional_offload"])

    def test_explicit_layer_cap_gets_its_own_fix(self):
        result = verdict(LAPTOP_PARTIAL.read_text(), ["--n-gpu-layers", "20"])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["requested_gpu_layers"], "20")
        self.assertIn("N_GPU_LAYERS=auto", result["fix_hint"])

    def test_auto_layers_keep_the_fit_hint(self):
        result = verdict(LAPTOP_PARTIAL.read_text(), ["--n-gpu-layers", "auto"])
        self.assertEqual(result["requested_gpu_layers"], "auto")
        self.assertIn("safety margin", result["fix_hint"])


def fit_log(projected: int, free: int, target: int, ubatch: int, compute: float, on_gpu: int = 30) -> str:
    """A spilled load in llama.cpp b9014's wording, with chosen fit numbers."""
    shortfall = projected - free + target
    return "\n".join([
        "main: loading model",
        f"common_params_fit_impl: projected to use {projected} MiB of device memory vs. {free} MiB of free device memory",
        f"common_params_fit_impl: cannot meet free memory target of {target} MiB, need to reduce device memory by {shortfall} MiB",
        f"load_tensors: offloaded {on_gpu}/33 layers to GPU",
        "load_tensors:   CPU_Mapped model buffer size =  1200.00 MiB",
        "load_tensors:        CUDA0 model buffer size =  4000.00 MiB",
        f"llama_context: n_ubatch      = {ubatch}",
        f"sched_reserve:      CUDA0 compute buffer size =   {compute:.2f} MiB",
        "sched_reserve:  CUDA_Host compute buffer size =   150.00 MiB",
        "main: model loaded",
    ])


class FixAdvice(unittest.TestCase):
    """The fix follows llama.cpp's own fit numbers, not a generic "free VRAM"."""

    def test_margin_and_micro_batch_can_make_room_without_freeing_the_gpu(self):
        # 7000 MiB needed, 6900 free: over budget even without a margin, but
        # -ub 256 halves the 1400 MiB compute buffer and a 512 MiB margin fits.
        result = verdict(fit_log(7000, 6900, 1024, 512, 1400))
        self.assertEqual(result["remedy"], "refit")
        self.assertIn("A 512 MiB margin and -ub 256", result["fix_hint"])
        self.assertIn("LLAMA_ARG_FIT_TARGET=512", result["fix_hint"])
        self.assertNotIn("close them", result["fix_hint"])

    def test_other_programs_holding_vram_get_the_free_or_shrink_fix(self):
        # tower1's F5: a leftover container held 20 GB; 20013 needed, 10810 free.
        result = verdict(fit_log(20013, 10810, 1024, 512, 500, on_gpu=16))
        self.assertEqual(result["remedy"], "free_or_shrink")
        self.assertIn("only 10810 MiB was free", result["fix_hint"])
        self.assertIn("close them", result["fix_hint"])
        self.assertNotIn("LLAMA_ARG_FIT_TARGET", result["fix_hint"])

    def test_refit_settings_already_in_effect_are_not_advised_again(self):
        # V1f settings and a desktop now holding 500 MiB of the laptop's GPU.
        result = verdict(fit_log(6246, 6360, 512, 256, 246.5, on_gpu=32))
        self.assertEqual(result["remedy"], "free_or_shrink")
        self.assertNotIn("LLAMA_ARG_UBATCH", result["fix_hint"])

    def test_no_fit_projection_gives_both_fixes(self):
        log = "load_tensors: offloaded 20/33 layers to GPU\nload_tensors:   CPU_Mapped model buffer size =  900.00 MiB\n"
        result = verdict(log)
        self.assertEqual(result["remedy"], "unknown")
        self.assertIn("LLAMA_ARG_FIT_TARGET", result["fix_hint"])
        self.assertIn("close them", result["fix_hint"])


# llama.cpp b9014 common/fit.cpp prints this per-device plan when it keeps a
# MoE layer on the GPU but moves its expert weights to system memory; the load
# log then still reports every layer as offloaded.
MOE_OVERFLOW_PLAN = (
    "common_params_fit_impl: projected to use 98000 MiB of device memory vs. 95000 MiB of free device memory\n"
    "common_params_fit_impl: cannot meet free memory target of 1024 MiB, need to reduce device memory by 4024 MiB\n"
    "common_params_fit_impl:   - CUDA0 (NVIDIA RTX PRO 6000 Blackwell Workstation Edition): "
    "49 layers (12 overflowing),  93950 MiB used,   1050 MiB free\n"
)


class HiddenSpills(unittest.TestCase):
    def test_moe_expert_overflow_fails_although_every_layer_is_offloaded(self):
        result = verdict(MOE_OVERFLOW_PLAN + TOWER2_RESIDENT.read_text())
        self.assertEqual((result["layers_on_gpu"], result["layers_total"]), (49, 49))
        self.assertEqual(result["moe_overflow_layers"], 12)
        self.assertEqual(result["status"], "fail")
        self.assertIn("MoE expert weights of 12 layers", result["message"])
        # Every layer is on the GPU: the message must not claim otherwise.
        self.assertIn("all 49 layers are on the GPU", result["message"])
        self.assertNotIn("partly on CPU", result["message"])
        self.assertIn("moved part of the model to the CPU", result["fix_hint"])

    def test_declared_moe_offload_explains_expert_overflow(self):
        result = verdict(MOE_OVERFLOW_PLAN + TOWER2_RESIDENT.read_text(), ["--n-cpu-moe", "12"])
        self.assertEqual(result["status"], "intentional")

    def test_fit_plan_without_overflow_is_not_a_spill(self):
        # The laptop's plan line has no "(N overflowing)" part.
        self.assertEqual(verdict(LAPTOP_RESIDENT.read_text())["moe_overflow_layers"], 0)

    def test_kv_cache_in_system_ram_fails_with_every_layer_offloaded(self):
        log = textwrap.dedent("""\
            load_tensors: offloaded 33/33 layers to GPU
            load_tensors:        CUDA0 model buffer size =  4861.28 MiB
            llama_kv_cache:        CPU KV buffer size =  1088.00 MiB
        """)
        for args in ([], ["--n-cpu-moe", "8"]):
            with self.subTest(args=args):
                result = verdict(log, args)
                self.assertEqual(result["status"], "fail")
                self.assertIn("1088 MiB of KV cache is in system RAM", result["message"])
                self.assertIn("all 33 layers are on the GPU", result["message"])


FAKE_DOCKER = """#!/usr/bin/env bash
set -euo pipefail
fixtures="$FAKE_DOCKER_FIXTURES"
if [[ "$1" == inspect ]]; then
    [[ -z "${FAKE_DOCKER_MISSING:-}" ]] || { echo "Error: no such object: $4" >&2; exit 1; }
    case "$3" in
        '{{.State.Running}}') echo true ;;
        '{{.State.StartedAt}}') echo 2026-09-25T12:06:55.123456789Z ;;
        '{{.Config.Image}}') echo "${FAKE_DOCKER_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda-b9014}" ;;
        '{{json .Args}}') echo '["--model","/models/Qwen3.5-9B-Q4_K_M.gguf","--n-gpu-layers","auto","--ctx-size","65536"]' ;;
        '{{json .Config.Env}}') echo '["LLAMA_ARG_CACHE_TYPE_K=q8_0","DASHBOARD_API_KEY=not-a-real-secret-fixture","LLAMA_ARG_N_CPU_MOE=0","PATH=/usr/bin"]' ;;
        *) exit 9 ;;
    esac
    exit 0
fi
if [[ "$1" == logs ]]; then
    [[ "$2" == --since && "$3" == 2026-09-25T12:06:55.123456789Z && "$4" == ods-llama-server ]] || exit 8
    cat "$fixtures"
    exit 0
fi
exit 7
"""


class DockerCollection(unittest.TestCase):
    def run_cli(self, log: Path, **extra_env: str) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as tmp:
            docker = Path(tmp) / "docker"
            docker.write_text(FAKE_DOCKER, encoding="utf-8")
            docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                   "FAKE_DOCKER_FIXTURES": str(log), **extra_env}
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--docker-container", "ods-llama-server"],
                env=env, capture_output=True, text=True, check=True,
            )
        return json.loads(completed.stdout), completed.stdout

    @unittest.skipIf(os.name == "nt", "fake docker is a bash script")
    def test_reads_the_current_container_run_only(self):
        result, raw = self.run_cli(LAPTOP_PARTIAL)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["source"], "docker:ods-llama-server")
        self.assertEqual(result["requested_gpu_layers"], "auto")
        self.assertEqual(result["offload_declarations"], [])
        # Only offload-related LLAMA_ARG_* values are read; nothing else from
        # the container environment may reach the report.
        self.assertNotIn("not-a-real-secret-fixture", raw)
        self.assertNotIn("DASHBOARD_API_KEY", raw)

    @unittest.skipIf(os.name == "nt", "fake docker is a bash script")
    def test_resident_container_passes(self):
        result, _ = self.run_cli(LAPTOP_RESIDENT)
        self.assertEqual(result["status"], "pass")

    @unittest.skipIf(os.name == "nt", "fake docker is a bash script")
    def test_lemonade_container_is_skipped(self):
        # AMD installs run Lemonade in the llama-server container; it does not
        # log placement, and that is not a failure.
        result, _ = self.run_cli(B11146_DEFAULT_VERBOSITY, FAKE_DOCKER_IMAGE="ods-lemonade-server:latest")
        self.assertEqual(result["status"], "skipped")
        self.assertIn("Lemonade", result["message"])

    @unittest.skipIf(os.name == "nt", "fake docker is a bash script")
    def test_managed_container_without_placement_lines_fails(self):
        result, _ = self.run_cli(B11146_DEFAULT_VERBOSITY)
        self.assertEqual(result["status"], "unverified")

    @unittest.skipIf(os.name == "nt", "fake docker is a bash script")
    def test_container_removed_mid_check_is_skipped(self):
        result, _ = self.run_cli(LAPTOP_PARTIAL, FAKE_DOCKER_MISSING="1")
        self.assertEqual(result["status"], "skipped")


class CommandLine(unittest.TestCase):
    def run_cli(self, *args: str) -> dict:
        completed = subprocess.run([sys.executable, str(SCRIPT), *args],
                                   capture_output=True, text=True, check=True)
        return json.loads(completed.stdout)

    def test_newest_existing_log_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            older, newer = Path(tmp) / "older.log", Path(tmp) / "newer.log"
            older.write_text(LAPTOP_RESIDENT.read_text(), encoding="utf-8")
            newer.write_text(LAPTOP_PARTIAL.read_text(), encoding="utf-8")
            os.utime(older, (1_000_000, 1_000_000))
            result = self.run_cli("--log-file", str(Path(tmp) / "missing.log"),
                                  "--log-file", str(older), "--log-file", str(newer))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["source"].endswith("newer.log"))

    def test_missing_logs_are_skipped(self):
        result = self.run_cli("--log-file", "/nonexistent/llama-server.log")
        self.assertEqual(result["status"], "skipped")

    def test_skip_and_unknown_modes(self):
        self.assertEqual(self.run_cli("--skip", "cpu install")["status"], "skipped")
        unknown = self.run_cli("--unknown", "docker failed")
        self.assertEqual((unknown["status"], unknown["message"]), ("unknown", "docker failed"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
