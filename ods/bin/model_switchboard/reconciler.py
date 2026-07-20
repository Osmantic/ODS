"""Runtime activation sequence for the ODS Model Switchboard (PR 2A).

One orchestration of the runtime phase of a model swap:

    stage -> verify_identity -> verify_completion

The reconciler owns sequencing and failure classification only. Snapshots,
consumer projection, HTTP contracts, and rollback proof remain with the host
agent's activation transaction until later slices migrate them. A phase
failure stops the sequence immediately and reports which boundary failed so
the caller's existing rollback machinery takes over.
"""

from __future__ import annotations

from typing import Any

from .adapters import RuntimeAdapter, result

PHASES = ("stage", "verify_identity", "verify_completion")


def run_runtime_activation(
    adapter: RuntimeAdapter,
    env: dict[str, str],
) -> dict[str, Any]:
    """Drive the runtime phases; first failure wins.

    Returns ``{"ok": bool, "phase": str, "detail": str, "identity": str|None}``.
    ``phase`` is the last phase attempted. Health-style success without an
    identity cannot satisfy verification: a verify_identity result that omits
    ``identity`` is treated as a failed boundary even when ``ok`` is true.
    """
    identity: str | None = None
    for phase in PHASES:
        try:
            outcome = getattr(adapter, phase)(env)
        except Exception as exc:  # adapter contract violation, not runtime state
            return {
                "ok": False,
                "phase": phase,
                "detail": f"adapter raised: {exc}",
                "identity": identity,
            }
        if not isinstance(outcome, dict) or "ok" not in outcome:
            return {
                "ok": False,
                "phase": phase,
                "detail": "adapter returned a non-contract result",
                "identity": identity,
            }
        if phase == "verify_identity" and outcome.get("ok") and not outcome.get("identity"):
            return {
                "ok": False,
                "phase": phase,
                "detail": "identity verification returned no concrete identity",
                "identity": identity,
            }
        if outcome.get("identity"):
            identity = str(outcome["identity"])
        if not outcome.get("ok"):
            return {
                "ok": False,
                "phase": phase,
                "detail": str(outcome.get("detail") or f"{phase} failed"),
                "identity": identity,
            }
    return {"ok": True, "phase": PHASES[-1], "detail": "runtime activation proven",
            "identity": identity}


__all__ = ["PHASES", "run_runtime_activation", "result"]
