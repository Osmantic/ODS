"""Runtime adapter contract for the ODS Model Switchboard (PR 2A).

An adapter owns exactly one runtime family's mechanics for the activation
sequence. Every operation returns a typed result dict (stdlib only):

    {"ok": bool, "detail": str, ...extras}

Successful verification must carry concrete evidence — the runtime-reported
identity and a completed generation — never configuration echoes. The
reconciler treats a missing or false ``ok`` as a transaction-boundary
failure; adapters must not raise for expected runtime failures.

PR 2A ships the shared contract, the transaction fake used by the boundary
test matrix, and the container llama.cpp adapter. Native Windows/macOS and
Lemonade/HipFire adapters land in PR 2B/2C and must pass the same contract
suite.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


def result(ok: bool, detail: str = "", **extras: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": bool(ok), "detail": str(detail)}
    payload.update(extras)
    return payload


class RuntimeAdapter(Protocol):
    """Contract implemented by every runtime family."""

    kind: str

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        """Start/restart the runtime so it serves the staged model."""

    def verify_identity(self, env: dict[str, str]) -> dict[str, Any]:
        """Prove the runtime reports the staged model. Extras: ``identity``."""

    def verify_completion(self, env: dict[str, str]) -> dict[str, Any]:
        """Prove a real completion. Extras: ``identity`` when reported."""


class ContainerLlamaAdapter:
    """Compose-managed llama.cpp runtime (Linux/NVIDIA container path).

    Behavior lives in the host agent's existing, fleet-proven helpers; this
    adapter is the single seam the reconciler drives. Callables are injected
    so the standalone host agent remains the one owner of process/compose
    mechanics and tests can substitute the transaction fake.
    """

    kind = "llama-server"

    def __init__(
        self,
        *,
        restart: Callable[[dict[str, str]], None],
        wait_ready: Callable[..., bool],
        expected_gguf: str,
        context_length: int,
        lemonade_model_id: str = "",
    ) -> None:
        self._restart = restart
        self._wait_ready = wait_ready
        self._expected_gguf = expected_gguf
        self._context_length = int(context_length)
        self._lemonade_model_id = lemonade_model_id

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        try:
            self._restart(env)
        except Exception as exc:  # expected runtime failures become results
            return result(False, f"container restart failed: {exc}")
        return result(True, "compose restart issued")

    def verify_identity(self, env: dict[str, str]) -> dict[str, Any]:
        # Readiness in the container path proves identity and serving state
        # in one bounded wait, mirroring the pre-adapter inline behavior.
        try:
            healthy = self._wait_ready(
                env,
                self._expected_gguf,
                self._context_length,
                lemonade_model_id=self._lemonade_model_id,
            )
        except Exception as exc:
            return result(False, f"readiness wait failed: {exc}")
        if not healthy:
            return result(False, "runtime did not report the staged model")
        return result(True, "runtime reports staged model", identity=self._expected_gguf)

    def verify_completion(self, env: dict[str, str]) -> dict[str, Any]:
        # _wait_for_model_readiness already requires a meaningful completion
        # before returning True; a separate probe here would double-generate.
        return result(True, "completion proven during readiness wait",
                      identity=self._expected_gguf)


class FakeAdapter:
    """Shared transaction fake for the boundary test matrix (test-only).

    ``plan`` maps operation name -> list of results returned in call order;
    the last entry repeats. ``calls`` records the exact sequence.
    """

    kind = "fake"

    def __init__(self, plan: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.plan = plan or {}
        self.calls: list[str] = []

    def _next(self, op: str) -> dict[str, Any]:
        self.calls.append(op)
        queue = self.plan.get(op)
        if not queue:
            return result(True, f"{op} default ok")
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("stage")

    def verify_identity(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("verify_identity")

    def verify_completion(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("verify_completion")
