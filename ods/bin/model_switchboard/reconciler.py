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

from datetime import datetime, timezone
from typing import Any

from .adapters import RuntimeAdapter, result

PHASES = ("stage", "verify_identity", "verify_completion")
_CAPABILITY_KEYS = {"chat", "tools", "vision", "agentViable"}


def _verification_error(outcome: dict[str, Any]) -> str:
    identity = outcome.get("identity")
    if not isinstance(identity, str) or not identity.strip():
        return "verification returned no concrete identity"
    context_length = outcome.get("contextLength")
    if (
        not isinstance(context_length, int)
        or isinstance(context_length, bool)
        or context_length <= 0
    ):
        return "verification returned no valid context length"
    if not isinstance(outcome.get("contextVerified"), bool):
        return "verification returned no context-verification status"
    capabilities = outcome.get("capabilities")
    if not isinstance(capabilities, dict) or set(capabilities) != _CAPABILITY_KEYS:
        return "verification returned no complete capability record"
    if any(not isinstance(capabilities[key], bool) for key in _CAPABILITY_KEYS):
        return "verification returned invalid capability values"
    verified_at = outcome.get("verifiedAt")
    if not isinstance(verified_at, str) or not verified_at.strip():
        return "verification returned no proof timestamp"
    try:
        parsed_time = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError:
        return "verification returned an invalid proof timestamp"
    if parsed_time.tzinfo is None or parsed_time.utcoffset() != timezone.utc.utcoffset(None):
        return "verification proof timestamp is not UTC"
    return ""


def run_runtime_activation(
    adapter: RuntimeAdapter,
    env: dict[str, str],
) -> dict[str, Any]:
    """Drive the runtime phases; first failure wins.

    Successful runs carry the runtime identity, context length, capabilities,
    and proof timestamp. ``phase`` is the last phase attempted. Health-style
    success without the complete proof contract cannot satisfy verification.
    """
    proof: dict[str, Any] = {
        "identity": None,
        "contextLength": None,
        "contextVerified": None,
        "capabilities": None,
        "verifiedAt": None,
    }
    for phase in PHASES:
        try:
            outcome = getattr(adapter, phase)(env)
        except Exception as exc:  # adapter contract violation, not runtime state
            return {
                "ok": False,
                "phase": phase,
                "detail": f"adapter raised: {exc}",
                **proof,
            }
        if not isinstance(outcome, dict) or "ok" not in outcome:
            return {
                "ok": False,
                "phase": phase,
                "detail": "adapter returned a non-contract result",
                **proof,
            }
        if phase.startswith("verify_") and outcome.get("ok"):
            contract_error = _verification_error(outcome)
            if contract_error:
                return {
                    "ok": False,
                    "phase": phase,
                    "detail": contract_error,
                    **proof,
                }
            outcome_identity = str(outcome["identity"]).strip()
            if phase == "verify_completion" and proof["identity"] != outcome_identity:
                return {
                    "ok": False,
                    "phase": phase,
                    "detail": "completion proof identity does not match identity proof",
                    **proof,
                }
            outcome_proof = {
                "identity": outcome_identity,
                "contextLength": int(outcome["contextLength"]),
                "contextVerified": bool(outcome["contextVerified"]),
                "capabilities": dict(outcome["capabilities"]),
                "verifiedAt": str(outcome["verifiedAt"]),
            }
            stable_fields = (
                "identity",
                "contextLength",
                "contextVerified",
                "capabilities",
            )
            if phase == "verify_completion" and any(
                outcome_proof[field] != proof[field] for field in stable_fields
            ):
                return {
                    "ok": False,
                    "phase": phase,
                    "detail": "completion proof metadata does not match identity proof",
                    **proof,
                }
            proof = outcome_proof
        if not outcome.get("ok"):
            return {
                "ok": False,
                "phase": phase,
                "detail": str(outcome.get("detail") or f"{phase} failed"),
                **proof,
            }
    return {
        "ok": True,
        "phase": PHASES[-1],
        "detail": "runtime activation proven",
        **proof,
    }


__all__ = ["PHASES", "run_runtime_activation", "result"]
