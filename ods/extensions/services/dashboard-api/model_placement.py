"""Where the running model's layers are: GPU, or partly the CPU.

The host agent reports llama-server's load-time placement as
``runtime.placement`` in its model status. Hosts that predate the field omit
it. Every consumer must treat a missing or malformed value as "unknown" and
never as "fits".

``unverified`` from the host agent is two different things:

- an ODS-managed llama-server (container or native) that finished loading but
  whose load log does not say where the layers are: a newer llama.cpp build or
  a log verbosity below 4. That is a failure: the model may be partly on the
  CPU and nothing would show it. It becomes the ``unverified`` state.
- Lemonade (which manages placement itself and does not log it), no running
  server log, or a load still in progress: nothing to judge, so unknown.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Host-agent placement status -> the state the dashboard shows.
_HOST_STATES = {
    "fully_resident": "resident",
    "intentional_offload": "intentional",
    "partial": "partial",
    "cpu_only": "cpu_only",
}
_RESIDENT_STATES = frozenset({"resident", "intentional"})
UNINTENDED_CPU_STATES = frozenset({"partial", "cpu_only"})
# States in which a measured speed is not known to be this GPU's speed.
_UNPROVEN_GPU_STATES = UNINTENDED_CPU_STATES | {"unverified"}
# Runtimes whose llama-server log ODS reads (bin/ods-host-agent.py
# _placement_runtime_kind); "lemonade" and "cpu" have no placement to prove.
_LOGGED_RUNTIMES = frozenset({"container", "native"})
_DETAIL_LIMIT = 400

# llama.cpp's defaults when ODS sets neither, and the settings ODS's residency
# profiles use (scripts/llama_gpu_residency.py has the same numbers).
_DEFAULT_FIT_TARGET_MIB = 1024
_DEFAULT_UBATCH = 512
_REFIT_FIT_TARGET_MIB = 512
_REFIT_UBATCH = 256


def _count(value: Any) -> Optional[int]:
    # bool is an int subclass; a layer count of True is malformed.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _mib(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _derived_state(on_gpu: int, fully_resident: bool, intentional: bool) -> str:
    # A declared MoE/tensor offload explains expert weights in system memory,
    # never whole layers on the CPU.
    if fully_resident:
        return "intentional" if intentional else "resident"
    return "cpu_only" if on_gpu == 0 else "partial"


def _malformed(placement: Any) -> None:
    logger.warning("Ignoring malformed runtime.placement from host agent: %r", placement)


def _detail(placement: dict) -> Optional[str]:
    detail = placement.get("reason")
    return detail[:_DETAIL_LIMIT] if isinstance(detail, str) and detail.strip() else None


def fit_remedy(placement: dict, layers_total: int) -> Optional[str]:
    """Which fix llama.cpp's own fit numbers point to for a spill.

    ``refit``: the model fits the free VRAM llama.cpp saw once its margin is
    512 MiB and -ub 256 shrinks the compute buffer, so nothing needs freeing
    (the 8 GB laptop: 6492 MiB needed, 6860 MiB free, 1024 MiB margin).
    ``free_or_shrink``: it does not fit even then, or those settings are
    already in effect. ``set_auto_layers``: N_GPU_LAYERS caps the layers.
    None when the host reported no fit projection.
    """
    requested = str(placement.get("nGpuLayers") or "").strip()
    if requested.isdigit() and int(requested) < layers_total:
        return "set_auto_layers"
    projected = _count(placement.get("projectedDeviceMiB"))
    free = _count(placement.get("freeDeviceMiB"))
    if projected is None or free is None:
        return None
    margin = _count(placement.get("fitTargetMiB")) or _DEFAULT_FIT_TARGET_MIB
    ubatch = _count(placement.get("ubatch")) or _DEFAULT_UBATCH
    compute = _mib(placement.get("gpuComputeMiB")) or 0.0
    refit_margin = min(margin, _REFIT_FIT_TARGET_MIB)
    refit_compute = compute * min(ubatch, _REFIT_UBATCH) / ubatch
    if refit_margin == margin and refit_compute >= compute:
        return "free_or_shrink"
    if projected - (compute - refit_compute) <= free - refit_margin:
        return "refit"
    return "free_or_shrink"


def _unverified(placement: dict) -> Optional[dict[str, Any]]:
    """The unverified state for a loaded ODS-managed server, else None."""
    if (
        placement.get("runtime") not in _LOGGED_RUNTIMES
        or placement.get("observationComplete") is not True
        or not placement.get("runtimeStartedAt")
    ):
        return None
    return {
        "layersOnGpu": None,
        "layersTotal": None,
        "cpuWeightMiB": None,
        "cpuKvMiB": None,
        "overflowingLayers": None,
        "fullyResident": None,
        "intentionalOffload": placement.get("intentionalOffload") is True,
        "state": "unverified",
        "remedy": None,
        "fitTargetMiB": None,
        "detail": _detail(placement),
    }


def runtime_placement(agent_status: Any) -> Optional[dict[str, Any]]:
    """Return a validated ``runtime.placement`` from host-agent status, or None."""
    if not isinstance(agent_status, dict):
        return None
    runtime = agent_status.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("placement") is None:
        return None
    placement = runtime["placement"]
    if not isinstance(placement, dict):
        _malformed(placement)
        return None
    status = placement.get("status")
    if status == "unverified":
        return _unverified(placement)
    if status == "not_applicable" or (
        placement.get("layersOnGpu") is None and placement.get("fullyResident") is None
    ):
        return None

    on_gpu = _count(placement.get("layersOnGpu"))
    total = _count(placement.get("layersTotal"))
    fully_resident = placement.get("fullyResident")
    intentional = placement.get("intentionalOffload", False)
    if (
        on_gpu is None
        or not total
        or on_gpu > total
        or not isinstance(fully_resident, bool)
        or not isinstance(intentional, bool)
        or (fully_resident and on_gpu < total)
    ):
        _malformed(placement)
        return None
    if status is None:
        state = _derived_state(on_gpu, fully_resident, intentional)
    else:
        state = _HOST_STATES.get(status)
        if state is None or (state in _RESIDENT_STATES) != fully_resident:
            _malformed(placement)
            return None
    spilled = state in UNINTENDED_CPU_STATES
    return {
        "layersOnGpu": on_gpu,
        "layersTotal": total,
        "cpuWeightMiB": _mib(placement.get("cpuWeightMiB")),
        # With every layer on the GPU these say what is in system memory
        # instead: KV cache, or MoE expert weights llama.cpp's fit moved.
        "cpuKvMiB": _mib(placement.get("cpuKvMiB")),
        "overflowingLayers": _count(placement.get("overflowingLayers")),
        "fullyResident": fully_resident,
        "intentionalOffload": intentional,
        "state": state,
        "remedy": fit_remedy(placement, total) if spilled else None,
        "fitTargetMiB": _count(placement.get("fitTargetMiB")) if spilled else None,
        "detail": _detail(placement),
    }


def is_unintended_cpu_placement(placement: Optional[dict[str, Any]]) -> bool:
    """True when part of the model runs on the CPU and nobody configured it."""
    return bool(placement) and placement["state"] in UNINTENDED_CPU_STATES


def is_unproven_gpu_placement(placement: Optional[dict[str, Any]]) -> bool:
    """True when a speed measured now may not be this GPU's speed for the model.

    A spill onto the CPU, or a loaded model whose placement the log does not
    state. None (a host that does not report placement) is not included.
    """
    return bool(placement) and placement["state"] in _UNPROVEN_GPU_STATES
