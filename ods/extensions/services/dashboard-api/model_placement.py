"""Where the running model's layers are: GPU, or partly the CPU.

The host agent reports llama-server's load-time placement as
``runtime.placement`` in its model status. Hosts that predate the field omit
it, and a host whose llama.cpp build does not log placement reports it as
unverified. Every consumer must treat a missing, unverified or malformed value
as "unknown" and never as "fits".
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
_DETAIL_LIMIT = 400


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
    if status == "unverified" or (placement.get("layersOnGpu") is None and placement.get("fullyResident") is None):
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
    detail = placement.get("reason")
    return {
        "layersOnGpu": on_gpu,
        "layersTotal": total,
        "cpuWeightMiB": _mib(placement.get("cpuWeightMiB")),
        "fullyResident": fully_resident,
        "intentionalOffload": intentional,
        "state": state,
        "detail": detail[:_DETAIL_LIMIT] if isinstance(detail, str) and detail.strip() else None,
    }


def is_unintended_cpu_placement(placement: Optional[dict[str, Any]]) -> bool:
    """True when part of the model runs on the CPU and nobody configured it."""
    return bool(placement) and placement["state"] in UNINTENDED_CPU_STATES
