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
             fail         layers, KV cache or MoE expert weights are in
                          system RAM and nothing declared it
             intentional  an MoE/tensor offload to the CPU was configured
             unverified   the model finished loading but the load log does
                          not say where the layers are (a llama.cpp build or
                          log verbosity that does not print placement). A
                          failure: an ODS-managed GPU llama-server must show
                          where its model is.
             unknown      nothing to judge yet: the model is still loading,
                          the load section rotated out of the log, or reading
                          the log failed
             skipped      no ODS-managed GPU llama-server to inspect
                          (Lemonade, external LLM, CPU-only, not running,
                          no log file)

Usage:
    llama_gpu_residency.py --docker-container ods-llama-server [--server-ready]
    llama_gpu_residency.py --log-file PATH [--log-file PATH ...] [--process-args] [--server-ready]
    llama_gpu_residency.py --skip "reason" | --unknown "reason"

``--server-ready`` tells the script the server answers /health, so the model
has finished loading even when the log has no "model loaded" line.

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

# llama.cpp log verbosity (common/log.h). From b9151 (ggml-org/llama.cpp#23021,
# "logs : reduce") llama.cpp maps the library's INFO messages to TRACE (4),
# above the default threshold of INFO (3). "offloaded N/M layers to GPU", the
# buffer sizes and common/fit.cpp's projection are such messages, so a b9151+
# server started at the default verbosity loads the model without logging
# where it went. llama-server reads the threshold from LLAMA_ARG_LOG_VERBOSITY
# from b9360 (#23778); b9151-b9357 read LLAMA_LOG_VERBOSITY. ODS passes
# LLAMA_ARG_LOG_VERBOSITY=4 (docker-compose.base.yml); b9357 and older ignore
# that name. tests/contracts/test-llama-placement-log.py ties every pinned
# llama.cpp build to a captured load log that this parser reads.
PLACEMENT_LOG_VERBOSITY = 4
TRACE_PLACEMENT_FROM_BUILD = 9151
ARG_VERBOSITY_ENV_FROM_BUILD = 9360
VERBOSITY_ENV = "LLAMA_ARG_LOG_VERBOSITY"

# llama-server logs one of these once per process start, before the model
# loads: b9150 and older print "main: loading model" and then
# "load_model: loading model '<path>'"; b9151 and newer only the second.
# Appended native logs hold several runs; only the newest one describes the
# running server.
_SESSION_START = re.compile(r"\b(?:main|load_model): loading model\b")
# The model finished loading ("main: model loaded" up to b9150,
# "llama_server: model loaded" after; both then log "listening on http://").
_READY = re.compile(r"\b(?:main|llama_server): model loaded\b|\blistening on https?://")
# "build_info: b9014-<sha>" (b9014), "build: 8210 (<sha>)" (b8210, b8248),
# "common_params_print_info: build 11146 (<sha>)" (verbosity 4 on b11146).
_BUILD = re.compile(r"\bbuild_info: b(\d+)\b|\bbuild:? (\d+) \(")
# Printed at the default verbosity from b9151 on.
_VERBOSITY = re.compile(r"\bverbosity = (\d+)\b")
_OFFLOADED = re.compile(r"\boffloaded (\d+)/(\d+) layers to GPU\b")
_MODEL_BUFFER = re.compile(r"\bload_tensors:\s+(\S+) model buffer size =\s+([0-9.]+) MiB")
_KV_BUFFER = re.compile(r"\bllama_kv_cache:\s+(\S+) KV buffer size =\s+([0-9.]+) MiB")
_COMPUTE_BUFFER = re.compile(r"\s(\S+) compute buffer size =\s+([0-9.]+) MiB")
_UBATCH = re.compile(r"\bllama_context: n_ubatch\s+= (\d+)")
_CONTEXT = re.compile(r"\bllama_context: n_ctx\s+= (\d+)")
# b9014 and b11146 prefix these with common_params_fit_impl, b8210/b8248 with
# llama_params_fit_impl.
_FIT_PROJECTION = re.compile(
    r"params_fit_impl: projected to use (\d+) MiB of device memory vs\. (\d+) MiB of free device memory"
)
_FIT_TARGET_MISSED = re.compile(
    r"params_fit_impl: cannot meet free memory target of (\d+) MiB, need to reduce device memory by (\d+) MiB"
)
_FIT_TARGET_MET = re.compile(r"params_fit_impl: will leave -?\d+ >= (\d+) MiB of free device memory")
# The fit plan per device (common/fit.cpp). For MoE models it can keep a layer
# on the GPU while moving that layer's expert weights to system memory:
# "- CUDA0 (...): 49 layers (12 overflowing), ...". The load log still says
# "offloaded 49/49 layers", so the overflow count is the only signal.
_FIT_DEVICE = re.compile(
    r"params_fit_impl:\s+- (.+?): +(\d+) layers(?: \( *(\d+) overflowing\))?, +(\d+) MiB used"
)
_RELEVANT = (
    _SESSION_START, _READY, _BUILD, _VERBOSITY, _OFFLOADED, _MODEL_BUFFER, _KV_BUFFER,
    _COMPUTE_BUFFER, _UBATCH, _CONTEXT, _FIT_PROJECTION, _FIT_TARGET_MISSED, _FIT_TARGET_MET,
    _FIT_DEVICE,
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

# llama.cpp's defaults when ODS sets neither: --fit-target 1024 (MiB kept
# free per GPU) and -ub 512.
DEFAULT_FIT_TARGET_MIB = 1024
DEFAULT_UBATCH = 512
# The fit settings ODS's residency profiles use: a 512 MiB margin and -ub 256,
# which halves the compute buffer (on the 8 GB laptop almost all of it holds
# the 248k-vocabulary output logits: 493 -> 246.5 MiB). Measured on the RTX
# 5070 Laptop: 33/33 layers instead of 29/33, same output.
REFIT_FIT_TARGET_MIB = 512
REFIT_UBATCH = 256
REFIT_ADVICE = (
    "Reload the model so ODS fits it to the GPU again with a smaller llama.cpp margin and "
    "micro-batch (dashboard Models page: Configure context on the running model, then Reload on GPU), "
    f"or set LLAMA_ARG_FIT_TARGET={REFIT_FIT_TARGET_MIB} and LLAMA_ARG_UBATCH={REFIT_UBATCH} in .env "
    "and run 'ods restart llama-server'."
)
FREE_OR_SHRINK_ADVICE = (
    "If other programs hold GPU memory (nvidia-smi lists them), close them and run "
    "'ods restart llama-server'. If nothing else uses the GPU, the model and context do not fit it: "
    "choose a smaller context or a smaller model on the dashboard Models page."
)
RECHECK_ADVICE = "Run 'ods restart llama-server' so the next load is logged, then re-run 'ods doctor'."


def is_relevant_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _RELEVANT)


def _in_host_memory(buffer_name: str) -> bool:
    # CPU, CPU_Mapped, CPU_REPACK, and pinned host buffers such as CUDA_Host.
    return buffer_name.upper().startswith("CPU") or buffer_name.endswith("_Host")


def _newest_start(lines: list[str]) -> int | None:
    starts = [index for index, line in enumerate(lines) if _SESSION_START.search(line)]
    return starts[-1] if starts else None


def _newest_session(lines: list[str]) -> list[str]:
    start = _newest_start(lines)
    return lines[start:] if start is not None else lines


def log_facts(log_text: str) -> dict:
    """What the log says about the newest llama-server run besides placement.

    ``load_started``: the run's start line is present. ``ready``: the run
    logged that the model finished loading. ``build`` and ``log_verbosity``
    come from the run's start banner, which llama.cpp prints before the load
    (b9151+ prints the build only at verbosity 4).
    """
    lines = log_text.splitlines()
    start = _newest_start(lines)
    session = lines[start:] if start is not None else lines
    # The banner precedes the start line; in an appended log the newest
    # banner before the newest start belongs to this run.
    banner = lines[: start + 1] if start is not None else lines
    build = verbosity = None
    for line in reversed(banner):
        if build is None and (match := _BUILD.search(line)):
            build = int(match.group(1) or match.group(2))
        if verbosity is None and (match := _VERBOSITY.search(line)):
            verbosity = int(match.group(1))
        if build is not None and verbosity is not None:
            break
    return {
        "load_started": start is not None,
        "ready": any(_READY.search(line) for line in session),
        "build": build,
        "log_verbosity": verbosity,
    }


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

    cpu_weights = gpu_weights = cpu_kv = gpu_kv = gpu_compute = 0.0
    ubatch = context = None
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
        compute = _COMPUTE_BUFFER.search(line)
        if compute and not _in_host_memory(compute.group(1)):
            gpu_compute += float(compute.group(2))
        if ubatch is None and (match := _UBATCH.search(line)):
            ubatch = int(match.group(1))
        if context is None and (match := _CONTEXT.search(line)):
            context = int(match.group(1))

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
        met = _FIT_TARGET_MET.search(line)
        if met and fit is not None and "target_mib" not in fit:
            fit["target_mib"] = int(met.group(1))
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
        "gpu_compute_buffer_mib": round(gpu_compute, 2),
        "ubatch": ubatch,
        "context_size": context,
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


def fit_remedy(placement: dict, requested: str | None) -> str:
    """Which fix applies to a spill, from llama.cpp's own fit numbers.

    ``set_auto_layers``  --n-gpu-layers was capped below the model's layers.
    ``refit``            the whole model fits the free GPU memory llama.cpp
                         saw once its margin is 512 MiB and -ub 256 shrinks the
                         compute buffer. Nothing needs freeing: the load that
                         used llama.cpp's defaults simply kept too much free.
                         The 8 GB laptop: 6492 MiB needed, 6860 MiB free,
                         1024 MiB margin, 493 MiB compute buffer at -ub 512.
    ``free_or_shrink``   even that does not fit, or those settings were already
                         in effect: other programs hold GPU memory (tower1's
                         F5: 20013 MiB needed, 10810 MiB free) or the model and
                         context are too large for the GPU.
    ``unknown``          llama.cpp logged no fit projection.
    """
    total = placement["layers_total"]
    if requested is not None and requested.strip().isdigit() and int(requested) < total:
        return "set_auto_layers"
    fit = placement.get("fit") or {}
    if "projected_mib" not in fit:
        return "unknown"
    projected, free = fit["projected_mib"], fit["free_mib"]
    margin = fit.get("target_mib", DEFAULT_FIT_TARGET_MIB)
    ubatch = placement.get("ubatch") or DEFAULT_UBATCH
    compute = placement.get("gpu_compute_buffer_mib") or 0.0
    refit_margin = min(margin, REFIT_FIT_TARGET_MIB)
    # The compute buffer grows with the micro-batch (the output logits are
    # n_vocab x ubatch floats); -ub 256 halves it from -ub 512.
    refit_compute = compute * min(ubatch, REFIT_UBATCH) / ubatch
    if refit_margin == margin and refit_compute >= compute:
        return "free_or_shrink"
    if projected - (compute - refit_compute) <= free - refit_margin:
        return "refit"
    return "free_or_shrink"


def _fail_hint(placement: dict, requested: str | None, remedy: str) -> str:
    on_gpu, total = placement["layers_on_gpu"], placement["layers_total"]
    if remedy == "set_auto_layers":
        return (
            f"The server was started with --n-gpu-layers {requested.strip()}, below the model's {total} layers. "
            "Set N_GPU_LAYERS=auto in .env, then run 'ods restart llama-server'."
        )
    moved = f"moved {total - on_gpu} layers to the CPU" if on_gpu < total else "moved part of the model to the CPU"
    if remedy == "unknown":
        return f"llama.cpp {moved} without logging a fit projection. {REFIT_ADVICE} {FREE_OR_SHRINK_ADVICE}"
    fit = placement["fit"]
    projected, free = fit["projected_mib"], fit["free_mib"]
    margin = fit.get("target_mib", DEFAULT_FIT_TARGET_MIB)
    if remedy == "refit" and projected <= free:
        return (
            f"The GPU had room for the whole model: llama.cpp needed {projected} MiB and {free} MiB was free, "
            f"but it keeps {margin} MiB free as a safety margin and {moved}. Closing other programs will "
            f"not change this. {REFIT_ADVICE}"
        )
    if remedy == "refit":
        return (
            f"llama.cpp needed {projected} MiB of GPU memory with {free} MiB free, kept its {margin} MiB "
            f"safety margin and {moved}. A {REFIT_FIT_TARGET_MIB} MiB margin and -ub {REFIT_UBATCH}, which "
            f"shrinks its {placement['gpu_compute_buffer_mib']:.0f} MiB compute buffer, leave room for the "
            f"whole model. {REFIT_ADVICE}"
        )
    return (
        f"llama.cpp needed {projected} MiB of GPU memory, but only {free} MiB was free when it loaded "
        f"and it keeps {margin} MiB free, so it {moved}. {FREE_OR_SHRINK_ADVICE}"
    )


def _spill(placement: dict) -> str | None:
    """Describe model state the GPU does not hold, or None when it holds all of it."""
    on_gpu, total = placement["layers_on_gpu"], placement["layers_total"]
    if on_gpu < total:
        return (f"model partly on CPU: {on_gpu}/{total} layers on GPU, "
                f"{placement['cpu_model_buffer_mib']:.0f} MiB of weights in system RAM")
    if placement["cpu_kv_buffer_mib"] > 0:
        return (f"part of the model is in system RAM: all {total} layers are on the GPU, but "
                f"{placement['cpu_kv_buffer_mib']:.0f} MiB of KV cache is in system RAM")
    if placement["moe_overflow_layers"] > 0:
        return (f"part of the model is in system RAM: all {total} layers are on the GPU, but llama.cpp "
                f"moved the MoE expert weights of {placement['moe_overflow_layers']} layers to system RAM")
    return None


def _unverified(base: dict, facts: dict) -> dict:
    """The model is loaded but the log does not say where it is: a failure."""
    build, verbosity = facts.get("build"), facts.get("log_verbosity")
    build_label = f"llama.cpp b{build}" if build else "this llama.cpp build"
    quiet = verbosity is not None and verbosity < PLACEMENT_LOG_VERBOSITY
    if quiet and build is not None and TRACE_PLACEMENT_FROM_BUILD <= build < ARG_VERBOSITY_ENV_FROM_BUILD:
        message = (f"llama-server loaded the model but logged at verbosity {verbosity}; {build_label} prints "
                   f"layer placement only at verbosity {PLACEMENT_LOG_VERBOSITY}, so ODS cannot confirm "
                   "the model is on the GPU")
        fix = (f"{build_label} reads LLAMA_LOG_VERBOSITY, which ODS does not pass. Run the llama-server "
               "image ODS pins (unset LLAMA_SERVER_IMAGE in .env), then run 'ods restart llama-server'.")
    elif quiet:
        message = (f"llama-server loaded the model but logged at verbosity {verbosity}; llama.cpp b"
                   f"{TRACE_PLACEMENT_FROM_BUILD} and newer print layer placement only at verbosity "
                   f"{PLACEMENT_LOG_VERBOSITY}, so ODS cannot confirm the model is on the GPU")
        fix = (f"Remove any {VERBOSITY_ENV} value below {PLACEMENT_LOG_VERBOSITY} from .env (ODS passes "
               f"{VERBOSITY_ENV}={PLACEMENT_LOG_VERBOSITY} by default), run 'ods restart llama-server', "
               "then re-run 'ods doctor'.")
    elif not facts.get("load_started"):
        message = ("llama-server loaded the model, but its log has no model-load section that ODS "
                   f"recognizes ({build_label}), so ODS cannot confirm the model is on the GPU")
        fix = ("Run the llama-server image ODS pins (unset LLAMA_SERVER_IMAGE in .env), then run "
               "'ods restart llama-server': ODS reads placement from the load logs of the llama.cpp "
               "builds it pins.")
    else:
        message = (f"llama-server loaded the model but its log does not say where the layers are "
                   f"({build_label} printed no 'offloaded N/M layers to GPU' line), so ODS cannot confirm "
                   "the model is on the GPU")
        fix = ("Run the llama-server image ODS pins (unset LLAMA_SERVER_IMAGE in .env), then run "
               "'ods restart llama-server': ODS reads placement from the load logs of the llama.cpp "
               "builds it pins.")
    return {**base, "status": "unverified", "message": message, "fix_hint": fix}


def evaluate(placement: dict | None, declarations: list[str], requested: str | None, source: str,
             facts: dict | None = None, server_ready: bool = False) -> dict:
    """Combine a parsed placement with the server's configuration into a verdict.

    A declared MoE/tensor offload explains expert weights in system memory; it
    never explains whole layers or KV cache on the CPU, which stay failures.

    Without a placement line: once the model has loaded (the log says so, or
    the server answers /health while the log still holds this run's load
    section) the result is ``unverified``, a failure. A llama.cpp upgrade or
    log setting that stops printing placement must fail loudly, not pass. It is
    ``unknown`` while the model is still loading, and when the log holds
    neither the start nor the end of this run's load: the load section rotated
    out, and a restart logs a fresh one.
    """
    facts = dict(facts or {})
    base = {
        "source": source,
        "requested_gpu_layers": requested,
        "offload_declarations": declarations,
        "intentional_offload": bool(declarations),
        "llama_build": facts.get("build"),
        "log_verbosity": facts.get("log_verbosity"),
    }
    if placement is None:
        load_logged = facts.get("load_started") or facts.get("ready")
        if load_logged and (facts.get("ready") or server_ready):
            return _unverified(base, facts)
        if load_logged or not server_ready:
            return {
                **base,
                "status": "unknown",
                "message": "llama-server has not finished loading the model, so its log does not "
                           "report layer placement yet",
                "fix_hint": "Re-run 'ods doctor' once llama-server answers /health.",
            }
        return {
            **base,
            "status": "unknown",
            "message": "the model-load section of llama-server's log rotated out, so ODS cannot re-check "
                       "where the model is",
            "fix_hint": RECHECK_ADVICE,
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
    remedy = fit_remedy(placement, requested)
    return {
        **result,
        "status": "fail",
        "remedy": remedy,
        "message": f"{spill}; expect much slower responses",
        "fix_hint": _fail_hint(placement, requested, remedy),
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


def judge(log_text: str, args: list[str], env: dict[str, str], source: str, server_ready: bool = False) -> dict:
    """Verdict for one llama-server run from its log, arguments and environment."""
    return evaluate(parse_placement(log_text), offload_declarations(args, env),
                    requested_gpu_layers(args, env), source, log_facts(log_text), server_ready)


def collect_docker(container: str, server_ready: bool = False) -> dict:
    try:
        running = _docker_format(container, "{{.State.Running}}")
    except subprocess.CalledProcessError:
        # Removed between the caller's check and this read (a model switch
        # recreates the container).
        return skipped(f"{container} does not exist")
    if running != "true":
        return skipped(f"{container} is not running")
    if "lemonade" in _docker_format(container, "{{.Config.Image}}").lower():
        # AMD installs run Lemonade (ods-lemonade-server) in the llama-server
        # container. It manages placement itself and does not log it.
        return skipped(f"{container} runs Lemonade, which manages GPU placement itself and does not log it")
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
    return judge(log_text, args, env, f"docker:{container}", server_ready)


def _native_server_args() -> list[str]:
    listing = subprocess.run(["ps", "-axo", "args="], check=True, capture_output=True,
                             text=True, timeout=30).stdout
    for line in listing.splitlines():
        tokens = line.split()
        if tokens and Path(tokens[0]).name == "llama-server" and "--model" in tokens:
            return shlex.split(line)
    return []


def collect_file(paths: list[Path], read_process_args: bool, server_ready: bool = False) -> dict:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return skipped("no native llama-server log found")
    newest = max(existing, key=lambda path: path.stat().st_mtime)
    with newest.open(encoding="utf-8", errors="replace") as handle:
        log_text = "\n".join(line.rstrip("\n") for line in handle if is_relevant_line(line))
    args = _native_server_args() if read_process_args else []
    return judge(log_text, args, {}, f"file:{newest}", server_ready)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--docker-container")
    mode.add_argument("--log-file", action="append", type=Path)
    mode.add_argument("--skip", metavar="REASON", help="report that no check applies")
    mode.add_argument("--unknown", metavar="REASON", help="report that placement could not be read")
    parser.add_argument("--process-args", action="store_true",
                        help="with --log-file, read offload flags from the running llama-server process")
    parser.add_argument("--server-ready", action="store_true",
                        help="the server answers /health, so the model has finished loading")
    options = parser.parse_args(argv)
    if options.skip is not None:
        result = skipped(options.skip)
    elif options.unknown is not None:
        result = unreadable(options.unknown)
    elif options.docker_container:
        result = collect_docker(options.docker_container, options.server_ready)
    else:
        result = collect_file(options.log_file, options.process_args, options.server_ready)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
