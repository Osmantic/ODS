#!/usr/bin/env python3
"""Report where the running llama-server placed the model's layers.

llama.cpp decides placement when it loads a model (``--n-gpu-layers auto``
plus its ``--fit`` pass) and reports the result only in its load log:
``load_tensors: offloaded 29/33 layers to GPU``. Neither ``/props`` nor
``/metrics`` exposes it, so a model that silently lands partly on the CPU
looks healthy everywhere else while decoding several times slower.

This script reads the load log of the running server and prints one JSON
object for ``ods doctor``:

    status   pass         every layer is on the GPU
             fail         layers are on the CPU and nothing declared it
             intentional  an MoE/tensor offload to the CPU was configured
             unknown      the log does not report placement (rotated out,
                          or a llama.cpp build that does not print it)
             skipped      no ODS-managed GPU llama-server to inspect

Usage:
    llama_gpu_residency.py --docker-container ods-llama-server
    llama_gpu_residency.py --log-file PATH [--log-file PATH ...] [--process-args]
    llama_gpu_residency.py --skip "reason" | --unknown "reason"

Only offload-related LLAMA_ARG_* values are read from the container
environment; nothing else from it is kept or printed.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# The server logs this once per process start, before the model loads.
# Appended native logs hold several runs; only the newest one describes the
# running server.
_SESSION_START = re.compile(r"\bmain: loading model\b")
_OFFLOADED = re.compile(r"\boffloaded (\d+)/(\d+) layers to GPU\b")
_MODEL_BUFFER = re.compile(r"\bload_tensors:\s+(\S+) model buffer size =\s+([0-9.]+) MiB")
_KV_BUFFER = re.compile(r"\bllama_kv_cache:\s+(\S+) KV buffer size =\s+([0-9.]+) MiB")
# b9014 prefixes these with common_params_fit_impl, b8210 with llama_params_fit_impl.
_FIT_PROJECTION = re.compile(
    r"params_fit_impl: projected to use (\d+) MiB of device memory vs\. (\d+) MiB of free device memory"
)
_FIT_TARGET_MISSED = re.compile(
    r"params_fit_impl: cannot meet free memory target of (\d+) MiB, need to reduce device memory by (\d+) MiB"
)
# The fit plan per device (common/fit.cpp). For MoE models it can keep a layer
# on the GPU while moving that layer's expert weights to system memory:
# "- CUDA0 (...): 49 layers (12 overflowing), ...". The load log still says
# "offloaded 49/49 layers", so the overflow count is the only signal.
_FIT_DEVICE = re.compile(
    r"params_fit_impl:\s+- (.+?): +(\d+) layers(?: \( *(\d+) overflowing\))?, +(\d+) MiB used"
)
_RELEVANT = (
    _SESSION_START, _OFFLOADED, _MODEL_BUFFER, _KV_BUFFER, _FIT_PROJECTION, _FIT_TARGET_MISSED, _FIT_DEVICE,
)

# Offload-related settings, by llama.cpp's own names (common/arg.cpp, b9014).
_OFFLOAD_ENV = ("LLAMA_ARG_N_CPU_MOE", "LLAMA_ARG_CPU_MOE", "LLAMA_ARG_OVERRIDE_TENSOR")
_GPU_LAYERS_ENV = "LLAMA_ARG_N_GPU_LAYERS"
_KEPT_ENV = frozenset(_OFFLOAD_ENV + (_GPU_LAYERS_ENV,))
_N_CPU_MOE_FLAGS = ("-ncmoe", "--n-cpu-moe")
_CPU_MOE_FLAGS = ("-cmoe", "--cpu-moe")
_OVERRIDE_TENSOR_FLAGS = ("-ot", "--override-tensor")
_GPU_LAYERS_FLAGS = ("-ngl", "--gpu-layers", "--n-gpu-layers")
_TRUTHY = frozenset({"1", "true", "on", "yes", "enabled"})

FIX_RESTART = (
    "Free GPU memory held by other programs (check nvidia-smi), then run "
    "'ods restart llama-server' and re-run 'ods doctor'. If the model still does "
    "not fit, choose a smaller context or a smaller model on the dashboard Models page."
)


def is_relevant_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _RELEVANT)


def _in_host_memory(buffer_name: str) -> bool:
    # CPU, CPU_Mapped, CPU_REPACK, and pinned host buffers such as CUDA_Host.
    return buffer_name.upper().startswith("CPU") or buffer_name.endswith("_Host")


def _newest_session(lines: list[str]) -> list[str]:
    starts = [index for index, line in enumerate(lines) if _SESSION_START.search(line)]
    return lines[starts[-1]:] if starts else lines


def parse_placement(log_text: str) -> dict | None:
    """Return the main model's placement from a llama-server log, or None.

    The first "offloaded" line after the newest process start belongs to the
    main model; a draft model, when configured, loads after it.
    """
    lines = _newest_session(log_text.splitlines())
    offload_index = next((i for i, line in enumerate(lines) if _OFFLOADED.search(line)), None)
    if offload_index is None:
        return None
    on_gpu, total = (int(value) for value in _OFFLOADED.search(lines[offload_index]).groups())
    next_offload = next(
        (i for i in range(offload_index + 1, len(lines)) if _OFFLOADED.search(lines[i])),
        len(lines),
    )
    main_model = lines[offload_index:next_offload]

    cpu_weights = gpu_weights = cpu_kv = gpu_kv = 0.0
    for line in main_model:
        buffer = _MODEL_BUFFER.search(line)
        if buffer:
            if _in_host_memory(buffer.group(1)):
                cpu_weights += float(buffer.group(2))
            else:
                gpu_weights += float(buffer.group(2))
        kv = _KV_BUFFER.search(line)
        if kv:
            if _in_host_memory(kv.group(1)):
                cpu_kv += float(kv.group(2))
            else:
                gpu_kv += float(kv.group(2))

    fit = None
    overflowing = 0
    for line in lines[:offload_index]:
        projection = _FIT_PROJECTION.search(line)
        if projection and fit is None:
            fit = {"projected_mib": int(projection.group(1)), "free_mib": int(projection.group(2))}
        missed = _FIT_TARGET_MISSED.search(line)
        if missed and fit is not None:
            fit["target_mib"] = int(missed.group(1))
            fit["shortfall_mib"] = int(missed.group(2))
        device = _FIT_DEVICE.search(line)
        if device and device.group(3):
            overflowing += int(device.group(3))

    return {
        "layers_on_gpu": on_gpu,
        "layers_total": total,
        # Token embeddings stay in host memory even when every layer is on the
        # GPU, so a non-zero CPU weight buffer alone is not a partial offload.
        "cpu_model_buffer_mib": round(cpu_weights, 2),
        "gpu_model_buffer_mib": round(gpu_weights, 2),
        "cpu_kv_buffer_mib": round(cpu_kv, 2),
        "gpu_kv_buffer_mib": round(gpu_kv, 2),
        # Layers whose MoE expert weights llama.cpp's fit moved to system memory.
        "moe_overflow_layers": overflowing,
        "fit": fit,
    }


def _flag_values(args: list[str], flags: tuple[str, ...]) -> list[str | None]:
    """Values given to any of ``flags``; None for a bare flag."""
    values: list[str | None] = []
    for index, arg in enumerate(args):
        name, sep, inline = arg.partition("=")
        if name not in flags:
            continue
        if sep:
            values.append(inline)
        elif index + 1 < len(args) and not args[index + 1].startswith("-"):
            values.append(args[index + 1])
        else:
            values.append(None)
    return values


def _positive_int(value: str | None) -> bool:
    return value is not None and value.strip().isdigit() and int(value) > 0


def _overrides_to_cpu(value: str | None) -> bool:
    return value is not None and re.search(r"=\s*cpu\b", value, re.IGNORECASE) is not None


def offload_declarations(args: list[str], env: dict[str, str]) -> list[str]:
    """Configured MoE/tensor offloads to the CPU, as operator-readable strings."""
    found: list[str] = []
    if _positive_int(env.get("LLAMA_ARG_N_CPU_MOE")):
        found.append(f"LLAMA_ARG_N_CPU_MOE={env['LLAMA_ARG_N_CPU_MOE'].strip()}")
    if str(env.get("LLAMA_ARG_CPU_MOE", "")).strip().lower() in _TRUTHY:
        found.append("LLAMA_ARG_CPU_MOE")
    if _overrides_to_cpu(env.get("LLAMA_ARG_OVERRIDE_TENSOR")):
        found.append("LLAMA_ARG_OVERRIDE_TENSOR")
    found.extend(f"--n-cpu-moe {value}" for value in _flag_values(args, _N_CPU_MOE_FLAGS) if _positive_int(value))
    if _flag_values(args, _CPU_MOE_FLAGS):
        found.append("--cpu-moe")
    if any(_overrides_to_cpu(value) for value in _flag_values(args, _OVERRIDE_TENSOR_FLAGS)):
        found.append("--override-tensor")
    return found


def requested_gpu_layers(args: list[str], env: dict[str, str]) -> str | None:
    values = [value for value in _flag_values(args, _GPU_LAYERS_FLAGS) if value is not None]
    if values:
        return values[-1]
    return env.get(_GPU_LAYERS_ENV)


def _fail_hint(placement: dict, requested: str | None) -> str:
    on_gpu, total = placement["layers_on_gpu"], placement["layers_total"]
    if requested is not None and requested.strip().isdigit() and int(requested) < total:
        return (
            f"The server was started with --n-gpu-layers {requested.strip()}, below the model's {total} layers. "
            "Set N_GPU_LAYERS=auto in .env, then run 'ods restart llama-server'."
        )
    fit = placement.get("fit") or {}
    if "target_mib" in fit:
        moved = (f"moved {total - on_gpu} layers to the CPU" if on_gpu < total
                 else "moved part of the model to the CPU")
        return (
            f"llama.cpp needed {fit['projected_mib']} MiB of GPU memory with {fit['free_mib']} MiB free, "
            f"kept its {fit['target_mib']} MiB safety margin, and {moved}. " + FIX_RESTART
        )
    return FIX_RESTART


def _spill(placement: dict) -> str | None:
    """Describe model state the GPU does not hold, or None when it holds all of it."""
    on_gpu, total = placement["layers_on_gpu"], placement["layers_total"]
    if on_gpu < total:
        return f"{on_gpu}/{total} layers on GPU"
    if placement["cpu_kv_buffer_mib"] > 0:
        return f"{on_gpu}/{total} layers on GPU but {placement['cpu_kv_buffer_mib']:.0f} MiB of KV cache in system RAM"
    if placement["moe_overflow_layers"] > 0:
        return (f"{on_gpu}/{total} layers on GPU but llama.cpp moved the MoE expert weights of "
                f"{placement['moe_overflow_layers']} layers to system RAM")
    return None


def evaluate(placement: dict | None, declarations: list[str], requested: str | None, source: str) -> dict:
    """Combine a parsed placement with the server's configuration into a verdict.

    A declared MoE/tensor offload explains expert weights in system memory; it
    never explains whole layers or KV cache on the CPU, which stay failures.
    """
    base = {
        "source": source,
        "requested_gpu_layers": requested,
        "offload_declarations": declarations,
        "intentional_offload": bool(declarations),
    }
    if placement is None:
        return {
            **base,
            "status": "unknown",
            "message": "llama-server's log does not report layer placement "
                       "(the load section rotated out, or this llama.cpp build does not print it)",
            "fix_hint": "Run 'ods restart llama-server', then re-run 'ods doctor' to check placement after a fresh load.",
        }
    on_gpu, total = placement["layers_on_gpu"], placement["layers_total"]
    result = {**base, **placement}
    spill = _spill(placement)
    if spill is None or (declarations and on_gpu >= total and placement["cpu_kv_buffer_mib"] == 0):
        if declarations:
            return {
                **result,
                "status": "intentional",
                "message": f"{on_gpu}/{total} layers on GPU; MoE/tensor offload to system RAM configured by "
                           f"{', '.join(declarations)}",
                "fix_hint": "",
            }
        return {**result, "status": "pass", "message": f"{on_gpu}/{total} layers on GPU", "fix_hint": ""}
    return {
        **result,
        "status": "fail",
        "message": f"model partly on CPU: {spill}, {placement['cpu_model_buffer_mib']:.0f} MiB of weights "
                   "in system RAM; expect much slower responses",
        "fix_hint": _fail_hint(placement, requested),
    }


def skipped(reason: str) -> dict:
    return {"status": "skipped", "source": None, "message": reason, "fix_hint": "",
            "intentional_offload": False, "offload_declarations": []}


def unreadable(reason: str) -> dict:
    return {**skipped(reason), "status": "unknown"}


def _docker_format(container: str, template: str) -> str:
    return subprocess.run(
        ["docker", "inspect", "--format", template, container],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def _relevant_output(command: list[str]) -> str:
    """Stream a command's combined output and keep only placement lines."""
    kept: list[str] = []
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace") as process:
        for line in process.stdout:
            if is_relevant_line(line):
                kept.append(line.rstrip("\n"))
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)
    return "\n".join(kept)


def collect_docker(container: str) -> dict:
    try:
        running = _docker_format(container, "{{.State.Running}}")
    except subprocess.CalledProcessError:
        # Removed between the caller's check and this read (a model switch
        # recreates the container).
        return skipped(f"{container} does not exist")
    if running != "true":
        return skipped(f"{container} is not running")
    started = _docker_format(container, "{{.State.StartedAt}}")
    args = json.loads(_docker_format(container, "{{json .Args}}") or "[]")
    raw_env = json.loads(_docker_format(container, "{{json .Config.Env}}") or "[]")
    env = {}
    for item in raw_env:
        key, _, value = item.partition("=")
        if key in _KEPT_ENV:
            env[key] = value
    # --since limits the read to the current process: a restarted container
    # keeps the earlier runs' output in the same log.
    log_text = _relevant_output(["docker", "logs", "--since", started, container])
    return evaluate(parse_placement(log_text), offload_declarations(args, env),
                    requested_gpu_layers(args, env), f"docker:{container}")


def _native_server_args() -> list[str]:
    listing = subprocess.run(["ps", "-axo", "args="], check=True, capture_output=True,
                             text=True, timeout=30).stdout
    for line in listing.splitlines():
        tokens = line.split()
        if tokens and Path(tokens[0]).name == "llama-server" and "--model" in tokens:
            return shlex.split(line)
    return []


def collect_file(paths: list[Path], read_process_args: bool) -> dict:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return skipped("no native llama-server log found")
    newest = max(existing, key=lambda path: path.stat().st_mtime)
    with newest.open(encoding="utf-8", errors="replace") as handle:
        log_text = "\n".join(line.rstrip("\n") for line in handle if is_relevant_line(line))
    args = _native_server_args() if read_process_args else []
    return evaluate(parse_placement(log_text), offload_declarations(args, {}),
                    requested_gpu_layers(args, {}), f"file:{newest}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--docker-container")
    mode.add_argument("--log-file", action="append", type=Path)
    mode.add_argument("--skip", metavar="REASON", help="report that no check applies")
    mode.add_argument("--unknown", metavar="REASON", help="report that placement could not be read")
    parser.add_argument("--process-args", action="store_true",
                        help="with --log-file, read offload flags from the running llama-server process")
    options = parser.parse_args(argv)
    if options.skip is not None:
        result = skipped(options.skip)
    elif options.unknown is not None:
        result = unreadable(options.unknown)
    elif options.docker_container:
        result = collect_docker(options.docker_container)
    else:
        result = collect_file(options.log_file, options.process_args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
