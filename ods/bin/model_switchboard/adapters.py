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

    def publish_native_alias(self, env: dict[str, str]) -> dict[str, Any]:
        """Publish a runtime-native alias when the backend supports one."""

    def unload(self, env: dict[str, str]) -> dict[str, Any]:
        """Unload the staged or previous runtime model."""

    def delete(self, env: dict[str, str]) -> dict[str, Any]:
        """Delete a non-active model artifact through the runtime."""

    def rollback(self, env: dict[str, str]) -> dict[str, Any]:
        """Restore the adapter's previously captured runtime state."""


class ReadinessProbe(Protocol):
    """Return runtime-carried identity, context, and proof time."""

    def __call__(
        self,
        env: dict[str, str],
        gguf_file: str,
        context_length: int,
        lemonade_model_id: str = "",
    ) -> dict[str, Any]: ...


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
        wait_ready: ReadinessProbe,
        expected_gguf: str,
        context_length: int,
        lemonade_model_id: str = "",
        capabilities: dict[str, bool] | None = None,
        unload: Callable[[dict[str, str]], None] | None = None,
        delete: Callable[[dict[str, str]], None] | None = None,
        rollback: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self._restart = restart
        self._wait_ready = wait_ready
        self._expected_gguf = expected_gguf
        self._context_length = int(context_length)
        self._lemonade_model_id = lemonade_model_id
        supplied_capabilities = capabilities or {}
        self._capabilities = {
            "chat": bool(supplied_capabilities.get("chat", True)),
            "tools": bool(supplied_capabilities.get("tools", False)),
            "vision": bool(supplied_capabilities.get("vision", False)),
            "agentViable": bool(supplied_capabilities.get("agentViable", False)),
        }
        self._unload = unload
        self._delete = delete
        self._rollback = rollback
        self._verified_identity = ""
        self._verified_context = 0
        self._context_verified = False
        self._verified_at = ""

    def _verification_result(self, detail: str) -> dict[str, Any]:
        return result(
            True,
            detail,
            identity=self._verified_identity,
            contextLength=self._verified_context,
            contextVerified=self._context_verified,
            capabilities=dict(self._capabilities),
            verifiedAt=self._verified_at,
        )

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        try:
            self._restart(env)
        except Exception as exc:  # expected runtime failures become results
            return result(False, str(exc))
        return result(True, "compose restart issued")

    def verify_identity(self, env: dict[str, str]) -> dict[str, Any]:
        # Readiness in the container path proves identity and serving state
        # in one bounded wait, mirroring the pre-adapter inline behavior.
        try:
            runtime_proof = self._wait_ready(
                env,
                self._expected_gguf,
                self._context_length,
                lemonade_model_id=self._lemonade_model_id,
            )
        except Exception as exc:
            return result(False, f"readiness wait failed: {exc}")
        if not isinstance(runtime_proof, dict):
            return result(False, "runtime did not return a proof record")
        runtime_identity = runtime_proof.get("identity")
        runtime_context = runtime_proof.get("contextLength")
        context_verified = runtime_proof.get("contextVerified")
        verified_at = runtime_proof.get("verifiedAt")
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            return result(False, "runtime did not report the staged model")
        if (
            not isinstance(runtime_context, int)
            or isinstance(runtime_context, bool)
            or runtime_context <= 0
        ):
            return result(False, "runtime did not report a valid context length")
        if not isinstance(context_verified, bool):
            return result(False, "runtime proof has no context-verification status")
        if not isinstance(verified_at, str) or not verified_at.strip():
            return result(False, "runtime proof has no timestamp")
        self._verified_identity = runtime_identity.strip()
        self._verified_context = runtime_context
        self._context_verified = context_verified
        self._verified_at = verified_at.strip()
        return self._verification_result(
            "runtime reports staged model and completed proof request"
        )

    def verify_completion(self, env: dict[str, str]) -> dict[str, Any]:
        # _wait_for_model_readiness returns an identity only after a meaningful
        # completion reports the same concrete model. Do not echo configuration.
        if not self._verified_identity:
            return result(False, "completion proof has no runtime identity")
        return self._verification_result("completion proven during readiness wait")

    def publish_native_alias(self, env: dict[str, str]) -> dict[str, Any]:
        return result(True, "native alias not required for llama.cpp")

    @staticmethod
    def _optional_operation(
        operation: str,
        callback: Callable[[dict[str, str]], None] | None,
        env: dict[str, str],
    ) -> dict[str, Any]:
        if callback is None:
            return result(False, f"{operation} is not configured")
        try:
            callback(env)
        except Exception as exc:
            return result(False, str(exc))
        return result(True, f"{operation} completed")

    def unload(self, env: dict[str, str]) -> dict[str, Any]:
        return self._optional_operation("unload", self._unload, env)

    def delete(self, env: dict[str, str]) -> dict[str, Any]:
        return self._optional_operation("delete", self._delete, env)

    def rollback(self, env: dict[str, str]) -> dict[str, Any]:
        return self._optional_operation("rollback", self._rollback, env)


class NativeLlamaAdapter(ContainerLlamaAdapter):
    """Native llama.cpp runtime (Windows process / macOS launchd+PID paths).

    Identical sequencing to the container adapter; only the injected restart
    mechanics differ (PR 2B). A distinct class keeps the runtime family
    explicit in state/evidence and lets 2C-era policy diverge if needed.
    """

    kind = "llama-server"


class LemonadeAdapter:
    """Deterministic Lemonade concrete-ID runtime (PR 2C).

    The shared container restart and the post-restart model-ID resolution
    run inline in the host agent (resolution requires a live server), so
    this adapter's ``stage`` is a proven no-op and verification drives the
    Lemonade-aware readiness wait. Native virtual ``collection.router``
    registration is PR 6, not here.
    """

    kind = "lemonade"

    def __init__(
        self,
        *,
        wait_ready: ReadinessProbe,
        expected_gguf: str,
        context_length: int,
        lemonade_model_id: str,
        capabilities: dict[str, bool] | None = None,
        unload: Callable[[dict[str, str]], None] | None = None,
        delete: Callable[[dict[str, str]], None] | None = None,
        rollback: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self._wait_ready = wait_ready
        self._expected_gguf = expected_gguf
        self._context_length = int(context_length)
        self._lemonade_model_id = lemonade_model_id
        supplied_capabilities = capabilities or {}
        self._capabilities = {
            "chat": bool(supplied_capabilities.get("chat", True)),
            "tools": bool(supplied_capabilities.get("tools", False)),
            "vision": bool(supplied_capabilities.get("vision", False)),
            "agentViable": bool(supplied_capabilities.get("agentViable", False)),
        }
        self._unload = unload
        self._delete = delete
        self._rollback = rollback
        self._verified_identity = ""
        self._verified_context = 0
        self._context_verified = False
        self._verified_at = ""

    def _verification_result(self, detail: str) -> dict[str, Any]:
        return result(
            True,
            detail,
            identity=self._verified_identity,
            contextLength=self._verified_context,
            contextVerified=self._context_verified,
            capabilities=dict(self._capabilities),
            verifiedAt=self._verified_at,
        )

    def stage(self, env: dict[str, str]) -> dict[str, Any]:
        # Restart + model-ID resolution already completed inline before the
        # reconciler runs; failure there raised before reaching this adapter.
        if not self._lemonade_model_id:
            return result(False, "no resolved Lemonade model id")
        return result(True, "lemonade runtime staged inline")

    def verify_identity(self, env: dict[str, str]) -> dict[str, Any]:
        try:
            runtime_proof = self._wait_ready(
                env,
                self._expected_gguf,
                self._context_length,
                lemonade_model_id=self._lemonade_model_id,
            )
        except Exception as exc:
            return result(False, f"lemonade readiness wait failed: {exc}")
        if not isinstance(runtime_proof, dict):
            return result(False, "lemonade runtime did not return a proof record")
        runtime_identity = runtime_proof.get("identity")
        runtime_context = runtime_proof.get("contextLength")
        context_verified = runtime_proof.get("contextVerified")
        verified_at = runtime_proof.get("verifiedAt")
        if not isinstance(runtime_identity, str) or not runtime_identity.strip():
            return result(False, "lemonade runtime did not report the staged model")
        if (
            not isinstance(runtime_context, int)
            or isinstance(runtime_context, bool)
            or runtime_context <= 0
        ):
            return result(False, "lemonade runtime did not report a valid context length")
        if not isinstance(context_verified, bool):
            return result(
                False, "lemonade runtime proof has no context-verification status"
            )
        if not isinstance(verified_at, str) or not verified_at.strip():
            return result(False, "lemonade runtime proof has no timestamp")
        self._verified_identity = runtime_identity.strip()
        self._verified_context = runtime_context
        self._context_verified = context_verified
        self._verified_at = verified_at.strip()
        return self._verification_result("lemonade runtime reports staged model")

    def verify_completion(self, env: dict[str, str]) -> dict[str, Any]:
        if not self._verified_identity:
            return result(False, "completion proof has no runtime identity")
        return self._verification_result(
            "completion proven during lemonade readiness wait"
        )

    def publish_native_alias(self, env: dict[str, str]) -> dict[str, Any]:
        return result(True, "native Lemonade alias is deferred to the route adapter")

    def unload(self, env: dict[str, str]) -> dict[str, Any]:
        return ContainerLlamaAdapter._optional_operation("unload", self._unload, env)

    def delete(self, env: dict[str, str]) -> dict[str, Any]:
        return ContainerLlamaAdapter._optional_operation("delete", self._delete, env)

    def rollback(self, env: dict[str, str]) -> dict[str, Any]:
        return ContainerLlamaAdapter._optional_operation(
            "rollback", self._rollback, env
        )


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

    def publish_native_alias(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("publish_native_alias")

    def unload(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("unload")

    def delete(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("delete")

    def rollback(self, env: dict[str, str]) -> dict[str, Any]:
        return self._next("rollback")
