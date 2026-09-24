"""Remote provider activation phase sequencing.

This is the same narrow boundary as the model switchboard reconciler: sequence
adapter calls, classify the first failing phase, and request rollback once the
commit phase starts. The adapter owns real side effects; this module stays pure
stdlib and does not perform network I/O.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


PHASES = ("stage", "validate", "commit", "prove")


def result(ok: bool, detail: str = "", **extras: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": bool(ok), "detail": str(detail)}
    payload.update(extras)
    return payload


class ActivationAdapter(Protocol):
    """Contract implemented by the future egress service activator."""

    kind: str

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        """Prepare candidate remote provider state without changing traffic."""

    def validate(self, env: dict[str, str]) -> dict[str, Any]:
        """Validate metadata, credentials, and transport readiness."""

    def commit(self, env: dict[str, str]) -> dict[str, Any]:
        """Switch egress service traffic to the candidate provider."""

    def prove(self, env: dict[str, str]) -> dict[str, Any]:
        """Prove the public alias completes through the committed route."""

    def rollback(self, env: dict[str, str]) -> dict[str, Any]:
        """Restore the previous committed route."""


def _rollback(adapter: ActivationAdapter, env: dict[str, str]) -> dict[str, Any]:
    try:
        outcome = adapter.rollback(env)
    except Exception as exc:
        return result(False, f"rollback raised: {exc}")
    if not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool:
        return result(False, "rollback returned a non-contract result")
    return outcome


def run_activation_transaction(
    adapter: ActivationAdapter,
    env: dict[str, str],
) -> dict[str, Any]:
    """Drive remote-provider activation phases; first failure wins."""
    committed = False
    for phase in PHASES:
        try:
            outcome = getattr(adapter, phase)(env)
        except Exception as exc:
            payload = result(False, f"adapter raised: {exc}", phase=phase)
            if committed or phase == "commit":
                payload["rollback"] = _rollback(adapter, env)
            return payload
        if not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool:
            payload = result(False, "adapter returned a non-contract result", phase=phase)
            if committed or phase == "commit":
                payload["rollback"] = _rollback(adapter, env)
            return payload
        if phase == "commit" and outcome.get("ok"):
            committed = True
        if not outcome.get("ok"):
            payload = result(
                False,
                str(outcome.get("detail") or f"{phase} failed"),
                phase=phase,
            )
            if committed or phase == "commit":
                payload["rollback"] = _rollback(adapter, env)
            return payload
    return result(True, "remote provider activation proven", phase=PHASES[-1])


class FakeActivationAdapter:
    """Shared transaction fake for remote-provider boundary tests."""

    kind = "fake"

    def __init__(
        self,
        plan: dict[str, list[dict[str, Any]]] | None = None,
        callbacks: dict[str, Callable[[dict[str, str]], None]] | None = None,
    ) -> None:
        self.plan = plan or {}
        self.callbacks = callbacks or {}
        self.calls: list[str] = []

    def _next(self, op: str, env: dict[str, str]) -> dict[str, Any]:
        self.calls.append(op)
        callback = self.callbacks.get(op)
        if callback is not None:
            callback(env)
        queue = self.plan.get(op)
        if not queue:
            return result(True, f"{op} default ok")
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("stage", env)

    def validate(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("validate", env)

    def commit(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("commit", env)

    def prove(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("prove", env)

    def rollback(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("rollback", env)


__all__ = [
    "PHASES",
    "ActivationAdapter",
    "FakeActivationAdapter",
    "result",
    "run_activation_transaction",
]
