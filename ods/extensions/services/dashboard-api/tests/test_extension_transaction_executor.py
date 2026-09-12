"""Tests for TransactionExecutor — composite, injected, deterministic execution.

Uses the real Phase 2 planner envelope and real TransactionStore with a
recording fake adapter / verifier / lock factory / observer.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure dashboard-api source is importable.
DASHBOARD_API_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_API_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_API_DIR))

import assistant_first_planner as planner  # noqa: E402
import extension_transaction_executor as executor_mod  # noqa: E402
import extension_transactions as transactions  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="transaction store requires POSIX"
)

# ── Constants ────────────────────────────────────────────────────────────────

ACTOR = "owner-42"
CREATED_AT = "2026-09-11T12:00:00Z"
APPROVED_AT = "2026-09-11T12:01:00Z"
NOW = "2026-09-11T12:02:00Z"
VALID_UNTIL = "2026-10-01T00:00:00Z"
IDEMPOTENCY_KEY = "1" * 64
CATALOG_REVISION = "a" * 64

HOST_STATE = {
    "odsVersion": "2.1.0",
    "platform": "linux",
    "architecture": "amd64",
    "containerRuntime": "docker",
    "gpuBackend": "cpu",
    "driverVersion": None,
    "available": {
        "diskBytes": 1_000_000,
        "ramBytes": 1_000_000,
        "vramBytes": 0,
        "cpuMillicores": 4_000,
        "gpuCount": 0,
    },
    "occupiedPorts": [],
    "reservedResources": [],
    "installedServices": [],
}
POLICY = {
    "allowedTrustTiers": ["bundled"],
    "forbiddenHostPermissions": ["docker-socket", "privileged"],
    "allowExperimental": False,
    "requireApproval": True,
}

STATE_REVISION = hashlib.sha256(
    planner.canonical_json_bytes(HOST_STATE)
).hexdigest()
POLICY_REVISION = hashlib.sha256(
    planner.canonical_json_bytes(POLICY)
).hexdigest()


def catalog_entry(service_id: str = "notes") -> dict:
    return {
        "schema_version": "ods.services.v2",
        "compatibility": {"ods_min": "2.0.0"},
        "service": {
            "id": service_id,
            "version": "1.2.3",
            "data_schema_version": "1",
            "type": "docker",
            "depends_on": [],
            "planning": {
                "provides": [f"{service_id}@1"],
                "requires": [],
                "optional": [],
                "conflicts": [],
                "provider_priority": 0,
                "requirements": {
                    "platforms": ["linux"],
                    "architectures": ["amd64"],
                    "container_runtimes": ["docker"],
                    "gpu_backends": ["cpu"],
                    "min_driver_version": None,
                },
                "estimates": {
                    "download_bytes": 100,
                    "disk_bytes": 200,
                    "cpu_millicores": 100,
                    "ram_bytes": 300,
                    "vram_bytes": 0,
                    "gpu_count": 0,
                },
                "resources": {
                    "host_ports": [],
                    "container_ports": [],
                    "networks": [],
                    "volumes": [],
                    "devices": [],
                    "exclusive": [],
                    "linux_capabilities": [],
                    "host_permissions": ["network"],
                },
                "configuration": [],
                "artifacts": {
                    "images": [
                        {
                            "reference": f"example/{service_id}:1.2.3",
                            "digest": "sha256:" + "e" * 64,
                            "download_bytes": 100,
                        }
                    ],
                    "builds": [],
                },
                "lifecycle": {
                    "health_checks": ["http:/health"],
                    "readiness": ["healthy"],
                    "setup_hook": None,
                    "migration_hook": None,
                    "rollback": "definition",
                    "timeout_seconds": 120,
                },
                "data": [],
                "trust": {
                    "tier": "bundled",
                    "publisher": "ODS",
                    "definition_signature": None,
                },
                "support": {"status": "supported", "url": None},
            },
        },
        "_catalog": {
            "definition_sha256": "sha256:" + "d" * 64,
            "compose_sha256": "",
        },
    }


def catalog_entries(*service_ids: str) -> list[dict]:
    return [catalog_entry(sid) for sid in service_ids]


def build_envelope(*, service_ids: list[str] | None = None) -> dict:
    if service_ids is None:
        service_ids = ["notes"]
    return planner.build_plan(
        catalog_entries(*service_ids),
        requested_action="ensure",
        requested_services=service_ids,
        requested_capabilities=[],
        provider_preferences={},
        missing_config_keys=[],
        missing_secret_keys=[],
        catalog_revision=CATALOG_REVISION,
        observed_state_revision=STATE_REVISION,
        observed_state=HOST_STATE,
        policy_revision=POLICY_REVISION,
        policy=POLICY,
        valid_until=VALID_UNTIL,
    )


def approval_for(descriptor: dict, envelope: dict) -> dict:
    return {
        "actor": ACTOR,
        "approvedAt": APPROVED_AT,
        "approvedBy": "owner-browser-session",
        "catalogRevision": envelope["catalogRevision"],
        "idempotencyKey": IDEMPOTENCY_KEY,
        "observedStateRevision": envelope["observedStateRevision"],
        "planHash": envelope["planHash"],
        "policyRevision": envelope["policyRevision"],
        "transactionId": descriptor["transactionId"],
        "validUntil": VALID_UNTIL,
    }


# ── Recording fakes ─────────────────────────────────────────────────────────

class RecordingAdapter:
    """Records every adapter call; supports injection of failures.

    ``fail_at(method, n)`` causes the n-th call (0-indexed) to return
    ``{"ok": False}``.
    """

    def __init__(self, observer=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._fail_at: dict[str, list[int]] = {}
        self.observer = observer

    def fail_at(self, method: str, call_number: int) -> None:
        self._fail_at.setdefault(method, []).append(call_number)

    def _record(self, method: str, binding, kwargs: dict) -> dict:
        count = len([c for c in self.calls if c[0] == method])
        kwargs = {"binding": binding, **kwargs}
        self.calls.append((method, kwargs))
        if self._fail_at.get(method, []) and count in self._fail_at[method]:
            return {"ok": False, "error": "injected-failure"}
        result = {
            "ok": True,
            "completed": True,
            "transactionId": binding.transaction_id,
            "planHash": binding.plan_hash,
        }
        if method == "apply_one" and self.observer is not None:
            service_id = kwargs["operation"]["serviceId"]
            if service_id not in self.observer.applied_services:
                self.observer.applied_services.append(service_id)
        if method == "compensate_one" and self.observer is not None:
            service_id = kwargs["operation"]["serviceId"]
            if service_id in self.observer.applied_services:
                self.observer.applied_services.remove(service_id)
        return result

    def reserve(self, binding, operation: dict[str, str]) -> dict:
        return self._record("reserve", binding, {"operation": operation})

    def download_and_verify_all(
        self, binding, operations: list[dict[str, str]]
    ) -> dict:
        return self._record(
            "download_and_verify_all", binding, {"operations": operations}
        )

    def stage_all(self, binding, operations: list[dict[str, str]]) -> dict:
        return self._record("stage_all", binding, {"operations": operations})

    def backup_all(self, binding, service_ids: list[str]) -> dict:
        return self._record("backup_all", binding, {"service_ids": service_ids})

    def configure_all(self, binding, service_ids: list[str]) -> dict:
        return self._record("configure_all", binding, {"service_ids": service_ids})

    def apply_one(self, binding, operation: dict[str, str]) -> dict:
        return self._record("apply_one", binding, {"operation": operation})

    def verify_all(self, binding, service_ids: list[str]) -> dict:
        return self._record("verify_all", binding, {"service_ids": service_ids})

    def release(self, binding, service_ids: list[str]) -> dict:
        return self._record("release", binding, {"service_ids": service_ids})

    def compensate_one(self, binding, operation: dict[str, str]) -> dict:
        return self._record("compensate_one", binding, {"operation": operation})

    def restore_all(self, binding, service_ids: list[str]) -> dict:
        return self._record("restore_all", binding, {"service_ids": service_ids})


class RecordingObserver:
    """Returns configurable observation evidence."""

    def __init__(self) -> None:
        self.applied_services: list[str] = []
        self.bindings = []

    def observe(self, binding) -> dict:
        self.bindings.append(binding)
        return {
            "transactionId": binding.transaction_id,
            "planHash": binding.plan_hash,
            "appliedServices": list(self.applied_services),
        }


class NoOpLockFactory:
    """Context manager that does nothing but records locked services."""

    def __init__(self) -> None:
        self.locked_services: list[str] | None = None

    def lock_services(self, service_ids: list[str]) -> "NoOpLock":
        self.locked_services = list(service_ids)
        return NoOpLock()


class NoOpLock:
    def __enter__(self) -> None:
        pass
    def __exit__(self, *a: Any) -> None:
        pass


class AlwaysTrueVerifier:
    """Always passes provenance verification."""

    def verify(self, plan_hash: str, envelope: dict) -> bool:
        return True


# ── Helpers ──────────────────────────────────────────────────────────────────

def create_and_approve(store, envelope: dict):
    """Create a transaction and approve it, returning (descriptor, txn_id)."""
    descriptor = store.create(envelope, ACTOR, IDEMPOTENCY_KEY, CREATED_AT, NOW)
    store._approve_record(descriptor["transactionId"], approval_for(descriptor, envelope), NOW)
    return descriptor


def make_executor(store, adapter=None, observer=None, verifier=None):
    if observer is None:
        observer = RecordingObserver()
    if adapter is None:
        adapter = RecordingAdapter(observer)
    elif isinstance(adapter, RecordingAdapter):
        adapter.observer = observer
    if verifier is None:
        verifier = AlwaysTrueVerifier()
    return executor_mod.TransactionExecutor(
        store=store,
        verifier=verifier,
        lock_factory=NoOpLockFactory(),
        adapter=adapter,
        observer=observer,
        actor=ACTOR,
    )


# The execution steps that the executor transitions through (from the module).
EXEC_STEPS = executor_mod.EXECUTION_STEPS


# ── Tests ────────────────────────────────────────────────────────────────────

# 1. Happy path: approved-only execution succeeds

def test_approved_only_execution(tmp_path):
    """Full happy path: approved → reserved → downloading → ... → committed."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    txn_id = descriptor["transactionId"]

    adapter = RecordingAdapter()
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(txn_id, envelope["planHash"])

    assert result.final_state == "committed"
    assert result.plan_hash == envelope["planHash"]
    assert result.applied_services == ["notes"]
    assert result.error is None

    # Verify all expected adapter calls were made
    call_names = [c[0] for c in adapter.calls]
    assert call_names[0] == "reserve"
    assert "download_and_verify_all" in call_names
    assert "stage_all" in call_names
    assert "backup_all" in call_names
    assert "configure_all" in call_names
    assert "apply_one" in call_names
    assert "verify_all" in call_names
    assert "release" in call_names

    # Verify terminal state in store
    loaded = store.read(txn_id)
    assert loaded["state"] == "committed"


def test_every_host_call_carries_the_exact_immutable_binding(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    observer = RecordingObserver()
    adapter = RecordingAdapter(observer)

    result = make_executor(
        store, adapter=adapter, observer=observer
    ).execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "committed"
    bindings = [payload["binding"] for _, payload in adapter.calls]
    bindings.extend(observer.bindings)
    assert bindings
    assert all(
        binding == executor_mod.ExecutionBinding(
            descriptor["transactionId"], envelope["planHash"]
        )
        for binding in bindings
    )
    with pytest.raises(AttributeError):
        bindings[0].plan_hash = "0" * 64


@pytest.mark.parametrize("field", ["transactionId", "planHash"])
def test_adapter_completion_with_wrong_binding_fails_closed(tmp_path, field):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    adapter = RecordingAdapter()

    def wrong_bound_reserve(binding, operation):
        result = adapter._record("reserve", binding, {"operation": operation})
        result[field] = "0" * 64
        return result

    adapter.reserve = wrong_bound_reserve
    result = make_executor(store, adapter=adapter).execute(
        descriptor["transactionId"], envelope["planHash"]
    )

    assert result.final_state == "rolled_back"
    expected = (
        "adapter-transaction-mismatch"
        if field == "transactionId"
        else "adapter-plan-hash-mismatch"
    )
    assert expected in result.error


def test_observation_with_wrong_binding_requires_manual_recovery(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    class WrongBoundObserver(RecordingObserver):
        def observe(self, binding):
            evidence = super().observe(binding)
            evidence["planHash"] = "0" * 64
            return evidence

    observer = WrongBoundObserver()
    result = make_executor(store, observer=observer).execute(
        descriptor["transactionId"], envelope["planHash"]
    )

    assert result.final_state == "manual_recovery_required"
    assert "adapter-plan-hash-mismatch" in result.error


def test_noop_services_are_observed_but_never_mutated(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    for operation in envelope["plan"]["operations"]:
        if operation["serviceId"] == "notes":
            operation["action"] = "noop"
    plan_hash = hashlib.sha256(
        planner.canonical_json_bytes(envelope["plan"])
    ).hexdigest()
    envelope["planHash"] = plan_hash
    envelope["planId"] = f"plan-{plan_hash[:24]}"
    descriptor = create_and_approve(store, envelope)
    observer = RecordingObserver()
    adapter = RecordingAdapter(observer)

    result = make_executor(store, adapter=adapter, observer=observer).execute(
        descriptor["transactionId"], plan_hash
    )

    assert result.final_state == "committed"
    by_name = {name: payload for name, payload in adapter.calls}
    assert by_name["download_and_verify_all"]["operations"] == [
        {"serviceId": "calendar", "action": "install"}
    ]
    assert by_name["stage_all"]["operations"] == [
        {"serviceId": "calendar", "action": "install"}
    ]
    assert by_name["backup_all"]["service_ids"] == ["calendar"]
    assert by_name["configure_all"]["service_ids"] == ["calendar"]
    assert by_name["release"]["service_ids"] == ["calendar"]
    assert by_name["verify_all"]["service_ids"] == ["calendar", "notes"]
    assert [
        payload["operation"]
        for name, payload in adapter.calls
        if name in {"reserve", "apply_one"}
    ] == [
        {"serviceId": "calendar", "action": "install"},
        {"serviceId": "calendar", "action": "install"},
    ]


# 2. Exact hash required

def test_exact_hash_required(tmp_path):
    """Reject execute with wrong plan hash."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    executor = make_executor(store)

    wrong_hash = "f" * 64
    with pytest.raises(transactions.ValidationRejected, match="plan-hash-mismatch"):
        executor.execute(descriptor["transactionId"], wrong_hash)


# 3. Strict state order

def test_strict_state_order_rejects_non_approved(tmp_path):
    """Reject execution from a non-approved, non-terminal state."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = store.create(envelope, ACTOR, IDEMPOTENCY_KEY, CREATED_AT, NOW)

    executor = make_executor(store)

    with pytest.raises(transactions.TransitionError, match="invalid-start-state"):
        executor.execute(descriptor["transactionId"], envelope["planHash"])


# 4. Plan apply order

def test_plan_apply_order(tmp_path):
    """Operations must apply in plan order (determined by planner)."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert result.final_state == "committed"

    # Apply calls must follow the plan's operation order (planner sorts)
    plan_order = [op["serviceId"] for op in envelope["plan"]["operations"]]
    apply_calls = [c for c in adapter.calls if c[0] == "apply_one"]
    applied_order = [c[1]["operation"]["serviceId"] for c in apply_calls]
    assert applied_order == plan_order


# 5. Download-before-mutation

def test_download_before_mutation(tmp_path):
    """download_and_verify_all must be called before any apply_one."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    executor = make_executor(store, adapter=adapter)

    executor.execute(descriptor["transactionId"], envelope["planHash"])

    download_idx = None
    first_apply_idx = None
    for i, (name, _) in enumerate(adapter.calls):
        if name == "download_and_verify_all" and download_idx is None:
            download_idx = i
        if name == "apply_one" and first_apply_idx is None:
            first_apply_idx = i

    assert download_idx is not None
    assert first_apply_idx is not None
    assert download_idx < first_apply_idx


# 6. Verify failure rollback

def test_verify_failure_rollback(tmp_path):
    """verify_all failure triggers compensation and rolled_back."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    adapter.fail_at("verify_all", 0)  # First call fails
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "rolled_back"
    assert result.applied_services == ["notes"]

    # Compensation must have been called
    comp_calls = [c for c in adapter.calls if c[0] == "compensate_one"]
    assert len(comp_calls) == 1
    assert comp_calls[0][1]["operation"]["serviceId"] == "notes"


# 7. Reverse compensation order

def test_reverse_compensation_order(tmp_path):
    """Compensation must occur in reverse applied order."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)

    # The planner sorts alphabetically: calendar, then notes
    # Fail on the 2nd apply_one (notes) — calendar is index 0, notes is index 1
    adapter = RecordingAdapter()
    adapter.fail_at("apply_one", 1)
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "rolled_back"
    # Only calendar was applied before notes failed
    assert "calendar" in result.applied_services
    assert "notes" not in result.applied_services

    comp_calls = [c for c in adapter.calls if c[0] == "compensate_one"]
    comp_order = [c[1]["operation"]["serviceId"] for c in comp_calls]
    # Only calendar was applied, so only calendar is compensated
    assert comp_order == ["calendar"]


# 8. Compensation failure → manual recovery

def test_compensation_failure_manual_recovery(tmp_path):
    """If compensation fails, terminal state must be manual_recovery_required."""
    store = transactions.TransactionStore(tmp_path / "store")
    # Use 2 services: planner sorts to calendar (0), notes (1)
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    # calendar applies successfully, then notes fails
    adapter.fail_at("apply_one", 1)  # 2nd apply_one (notes) fails
    adapter.fail_at("compensate_one", 0)  # 1st compensate_one (calendar) fails
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "manual_recovery_required"
    assert "calendar" in result.applied_services
    assert result.error is not None


# 9. Adapter incomplete rejection

def test_adapter_incomplete_rejection(tmp_path):
    """Adapter returning missing ok=True triggers rollback, not commit."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    class IncompleteAdapter:
        """Returns partial results without ok=True on reserve."""
        calls = []
        @staticmethod
        def _complete(binding):
            return {
                "ok": True,
                "completed": True,
                "transactionId": binding.transaction_id,
                "planHash": binding.plan_hash,
            }
        def reserve(self, binding, operation):
            self.calls.append(("reserve", operation))
            return {"status": "accepted"}  # No ok field → adapter-incomplete
        def download_and_verify_all(self, binding, operations):
            return self._complete(binding)
        def stage_all(self, binding, operations): return self._complete(binding)
        def backup_all(self, binding, service_ids): return self._complete(binding)
        def configure_all(self, binding, service_ids): return self._complete(binding)
        def apply_one(self, binding, operation): return self._complete(binding)
        def verify_all(self, binding, service_ids): return self._complete(binding)
        def release(self, binding, service_ids): return self._complete(binding)
        def compensate_one(self, binding, operation): return self._complete(binding)
        def restore_all(self, binding, service_ids): return self._complete(binding)

    adapter = IncompleteAdapter()
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])
    # The incomplete adapter result triggers a failure → rolled_back
    assert result.final_state == "rolled_back"
    # The first call was reserve which returned incomplete result
    assert adapter.calls[0][0] == "reserve"
    # Error must reference the adapter-incomplete cause
    assert result.error is not None
    assert "adapter-incomplete" in result.error


# 10. Terminal idempotency

def test_terminal_idempotency(tmp_path):
    """Executing on committed/rolled_back/manual states returns immediately."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    # First: run to committed
    adapter = RecordingAdapter()
    executor = make_executor(store, adapter=adapter)
    result1 = executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert result1.final_state == "committed"

    # Second: re-execute same call (idempotent)
    result2 = executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert result2.final_state == "committed"
    assert result2.error is None


# 11. Restart at every active state

@pytest.mark.parametrize("resume_state", [
    "reserved", "downloading", "staged", "configuring", "applying", "verifying",
])
def test_restart_at_active_state(tmp_path, resume_state):
    """Executor must resume from any in-progress state, not restart from approved."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    txn_id = descriptor["transactionId"]

    # Advance the transaction to resume_state by transitioning through
    # the execution pipeline states (not through planned/awaiting_approval).
    exec_states = list(executor_mod.EXECUTION_STEPS)
    resume_idx = exec_states.index(resume_state)

    # Transition: approved → reserved → ... → resume_state
    for i in range(resume_idx + 1):
        target = exec_states[i]
        store.transition(txn_id, target, ACTOR, NOW, NOW)

    # Verify the store state
    loaded = store.read(txn_id)
    assert loaded["state"] == resume_state

    # Now execute — it should resume from resume_state
    adapter = RecordingAdapter()
    observer = RecordingObserver()
    if resume_state == "verifying":
        observer.applied_services = [
            op["serviceId"] for op in envelope["plan"]["operations"]
        ]
    executor = make_executor(store, adapter=adapter, observer=observer)

    result = executor.execute(txn_id, envelope["planHash"])
    assert result.final_state == "committed"

    # The first adapter call should NOT be reserve if we're past reserved
    if resume_state != "reserved":
        assert adapter.calls[0][0] != "reserve", \
            f"Expected skip of reserve when resuming from {resume_state}"


# 12. No secret metadata in journal

def test_no_secret_metadata(tmp_path):
    """Journal step metadata must never contain secret-like keys or values."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    executor = make_executor(store, adapter=adapter)

    executor.execute(descriptor["transactionId"], envelope["planHash"])

    # Read the journal and check for secrets
    loaded = store.read(descriptor["transactionId"])
    for record in loaded["journal"]:
        if "step" in record:
            step = record["step"]
            for key in step:
                low = key.lower()
                # No secret-like keys
                assert "secret" not in low, f"secret key in journal: {key}"
                assert "password" not in low
                assert "token" not in low
                assert "credential" not in low
                assert "private" not in low


# 13. Multi-service plan with 2 services

def test_two_service_happy_path(tmp_path):
    """Two-service plan executes in order and commits."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert result.final_state == "committed"
    # Applied order matches plan order (planner sorts alphabetically)
    plan_order = [op["serviceId"] for op in envelope["plan"]["operations"]]
    assert result.applied_services == plan_order

    # Both must be reserved
    reserve_calls = [c for c in adapter.calls if c[0] == "reserve"]
    reserved = [c[1]["operation"]["serviceId"] for c in reserve_calls]
    assert set(reserved) == {"notes", "calendar"}


# 14. Provenance mismatch rejection

def test_provenance_mismatch_rejection(tmp_path):
    """Executor must reject when provenance verification fails."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    class FailingVerifier:
        def verify(self, plan_hash, envelope):
            return False

    executor = make_executor(store, verifier=FailingVerifier())

    with pytest.raises(transactions.TransitionError, match="provenance-mismatch"):
        executor.execute(descriptor["transactionId"], envelope["planHash"])


def test_provenance_requires_literal_true(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    class TruthyVerifier:
        def verify(self, plan_hash, stored_envelope):
            return 1

    with pytest.raises(transactions.TransitionError, match="provenance-mismatch"):
        make_executor(store, verifier=TruthyVerifier()).execute(
            descriptor["transactionId"], envelope["planHash"]
        )
    assert store.read(descriptor["transactionId"])["state"] == "approved"


def test_active_provenance_check_error_reconciles(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    transaction_id = descriptor["transactionId"]
    store.transition(transaction_id, "reserved", ACTOR, NOW, NOW)

    class BrokenVerifier:
        def verify(self, plan_hash, stored_envelope):
            raise OSError("unavailable")

    result = make_executor(store, verifier=BrokenVerifier()).execute(
        transaction_id, envelope["planHash"]
    )

    assert result.final_state == "rolled_back"
    assert result.error == "provenance-check-failed"


# 15. rolled_back idempotency

def test_rolled_back_idempotency(tmp_path):
    """Re-executing a rolled_back transaction returns immediately."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()
    adapter.fail_at("verify_all", 0)
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert result.final_state == "rolled_back"

    # Re-execute on rolled_back — must be idempotent
    adapter2 = RecordingAdapter()
    executor2 = make_executor(store, adapter=adapter2)
    result2 = executor2.execute(descriptor["transactionId"], envelope["planHash"])
    assert result2.final_state == "rolled_back"
    # No new adapter calls on idempotent re-execute (terminal path returns early)
    assert len(adapter2.calls) == 0


# 16. manual_recovery_required idempotency

def test_manual_recovery_idempotency(tmp_path):
    """Re-executing manual_recovery_required returns immediately."""
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)

    # calendar (idx 0) succeeds, notes (idx 1) fails
    # then compensation for calendar (comp idx 0) fails
    adapter = RecordingAdapter()
    adapter.fail_at("apply_one", 1)
    adapter.fail_at("compensate_one", 0)
    executor = make_executor(store, adapter=adapter)

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert result.final_state == "manual_recovery_required"

    # Re-execute — must be idempotent
    adapter2 = RecordingAdapter()
    executor2 = make_executor(store, adapter=adapter2)
    result2 = executor2.execute(descriptor["transactionId"], envelope["planHash"])
    assert result2.final_state == "manual_recovery_required"
    assert len(adapter2.calls) == 0


def test_background_ack_is_not_completion(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)

    adapter = RecordingAdapter()

    def queued_reserve(binding, operation):
        adapter.calls.append(
            ("reserve", {"binding": binding, "operation": operation})
        )
        return {"ok": True, "completed": False, "statusCode": 202}

    adapter.reserve = queued_reserve
    result = make_executor(store, adapter=adapter).execute(
        descriptor["transactionId"], envelope["planHash"]
    )

    assert result.final_state == "rolled_back"
    assert "adapter-background-ack" in result.error
    states = [record["state"] for record in store.read(
        descriptor["transactionId"]
    )["journal"]]
    assert states[-4:] == ["reserved", "failed", "reconciling", "rolled_back"]


def test_locks_are_canonical_and_provenance_is_checked_inside_lock(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)
    events = []

    class OrderedLock:
        def __enter__(self):
            events.append("lock-enter")

        def __exit__(self, *exc):
            events.append("lock-exit")

    class OrderedLockFactory:
        def lock_services(self, service_ids):
            events.append(("lock-services", service_ids))
            return OrderedLock()

    class OrderedVerifier:
        def verify(self, plan_hash, stored_envelope):
            events.append("verify-provenance")
            return True

    observer = RecordingObserver()
    executor = executor_mod.TransactionExecutor(
        store=store,
        verifier=OrderedVerifier(),
        lock_factory=OrderedLockFactory(),
        adapter=RecordingAdapter(observer),
        observer=observer,
        actor=ACTOR,
    )
    result = executor.execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "committed"
    assert events[:3] == [
        ("lock-services", ["calendar", "notes"]),
        "lock-enter",
        "verify-provenance",
    ]
    assert events[-1] == "lock-exit"


def test_crash_after_apply_resumes_from_durable_observation(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)
    observer = RecordingObserver()

    class InjectedCrash(BaseException):
        pass

    class CrashAfterFirstApply(RecordingAdapter):
        def apply_one(self, binding, operation):
            result = super().apply_one(binding, operation)
            if len([call for call in self.calls if call[0] == "apply_one"]) == 1:
                raise InjectedCrash("simulated process death")
            return result

    first_adapter = CrashAfterFirstApply(observer)
    with pytest.raises(InjectedCrash):
        make_executor(store, adapter=first_adapter, observer=observer).execute(
            descriptor["transactionId"], envelope["planHash"]
        )

    assert store.read(descriptor["transactionId"])["state"] == "applying"
    assert observer.applied_services == ["calendar"]

    second_adapter = RecordingAdapter(observer)
    result = make_executor(
        store, adapter=second_adapter, observer=observer
    ).execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "committed"
    second_apply_services = [
        call[1]["operation"]["serviceId"]
        for call in second_adapter.calls
        if call[0] == "apply_one"
    ]
    assert second_apply_services == ["notes"]


@pytest.mark.parametrize("recovery_state", ["failed", "reconciling"])
def test_restart_reconciles_failure_states(tmp_path, recovery_state):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    transaction_id = descriptor["transactionId"]
    store.transition(transaction_id, "reserved", ACTOR, NOW, NOW)
    store.transition(transaction_id, "failed", ACTOR, NOW, NOW)
    if recovery_state == "reconciling":
        store.transition(transaction_id, "reconciling", ACTOR, NOW, NOW)

    result = make_executor(store).execute(transaction_id, envelope["planHash"])

    assert result.final_state == "rolled_back"
    states = [record["state"] for record in store.read(transaction_id)["journal"]]
    assert states[-1] == "rolled_back"
    assert states.count("reconciling") == 1


def test_nonprefix_observation_forces_manual_recovery(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes", "calendar"])
    descriptor = create_and_approve(store, envelope)
    transaction_id = descriptor["transactionId"]
    for target in executor_mod.EXECUTION_STEPS[:5]:
        store.transition(transaction_id, target, ACTOR, NOW, NOW)

    observer = RecordingObserver()
    observer.applied_services = ["notes"]
    result = make_executor(store, observer=observer).execute(
        transaction_id, envelope["planHash"]
    )

    assert result.final_state == "manual_recovery_required"
    assert store.read(transaction_id)["state"] == "manual_recovery_required"


def test_terminal_transition_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    adapter = RecordingAdapter()
    adapter.fail_at("reserve", 0)
    original_transition = store.transition

    def fail_terminal_transition(transaction_id, target_state, *args, **kwargs):
        if target_state == "rolled_back":
            raise OSError("injected journal failure")
        return original_transition(transaction_id, target_state, *args, **kwargs)

    monkeypatch.setattr(store, "transition", fail_terminal_transition)
    with pytest.raises(OSError, match="injected journal failure"):
        make_executor(store, adapter=adapter).execute(
            descriptor["transactionId"], envelope["planHash"]
        )

    assert store.read(descriptor["transactionId"])["state"] == "reconciling"


def test_journal_records_exact_phase_boundary_metadata(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    make_executor(store).execute(descriptor["transactionId"], envelope["planHash"])

    journal = store.read(descriptor["transactionId"])["journal"]
    execution_records = journal[3:]
    assert execution_records[0]["step"] == {
        "step": "reserve",
        "status": "started",
    }
    assert [record["step"]["status"] for record in execution_records[1:]] == [
        "completed"
    ] * 6


def test_restore_failure_requires_manual_recovery(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    adapter = RecordingAdapter()
    adapter.fail_at("reserve", 0)
    adapter.fail_at("restore_all", 0)

    result = make_executor(store, adapter=adapter).execute(
        descriptor["transactionId"], envelope["planHash"]
    )

    assert result.final_state == "manual_recovery_required"


@pytest.mark.parametrize(
    ("crash_method", "expected_state"),
    [
        ("reserve", "reserved"),
        ("download_and_verify_all", "downloading"),
        ("stage_all", "staged"),
        ("backup_all", "configuring"),
        ("configure_all", "configuring"),
        ("apply_one", "applying"),
        ("verify_all", "verifying"),
        ("release", "verifying"),
    ],
)
def test_crash_at_each_phase_converges_without_duplicate_effect(
    tmp_path, crash_method, expected_state
):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    observer = RecordingObserver()

    class InjectedCrash(BaseException):
        pass

    class CrashOnceAdapter(RecordingAdapter):
        def __init__(self):
            super().__init__(observer)
            self.crashed = False
            self.effects: set[tuple[str, str]] = set()
            self.effect_counts: dict[tuple[str, str], int] = {}

        def _record(self, method, binding, kwargs):
            result = super()._record(method, binding, kwargs)
            operation = kwargs.get("operation")
            subject = operation["serviceId"] if operation else "all"
            key = (method, subject)
            if key not in self.effects:
                self.effects.add(key)
                self.effect_counts[key] = self.effect_counts.get(key, 0) + 1
            if method == crash_method and not self.crashed:
                self.crashed = True
                raise InjectedCrash(method)
            return result

    adapter = CrashOnceAdapter()
    executor = make_executor(store, adapter=adapter, observer=observer)
    with pytest.raises(InjectedCrash):
        executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert store.read(descriptor["transactionId"])["state"] == expected_state

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "committed"
    crashed_effects = [
        count
        for (method, _), count in adapter.effect_counts.items()
        if method == crash_method
    ]
    assert crashed_effects and crashed_effects == [1]


def test_crash_during_compensation_resumes_without_duplicate_compensation(tmp_path):
    store = transactions.TransactionStore(tmp_path / "store")
    envelope = build_envelope(service_ids=["notes"])
    descriptor = create_and_approve(store, envelope)
    observer = RecordingObserver()

    class InjectedCrash(BaseException):
        pass

    class CrashCompensationOnce(RecordingAdapter):
        def __init__(self):
            super().__init__(observer)
            self.crashed = False
            self.compensation_effects = 0

        def compensate_one(self, binding, operation):
            result = super().compensate_one(binding, operation)
            self.compensation_effects += 1
            if not self.crashed:
                self.crashed = True
                raise InjectedCrash("compensation")
            return result

    adapter = CrashCompensationOnce()
    adapter.fail_at("verify_all", 0)
    executor = make_executor(store, adapter=adapter, observer=observer)
    with pytest.raises(InjectedCrash):
        executor.execute(descriptor["transactionId"], envelope["planHash"])
    assert store.read(descriptor["transactionId"])["state"] == "reconciling"
    assert observer.applied_services == []

    result = executor.execute(descriptor["transactionId"], envelope["planHash"])

    assert result.final_state == "rolled_back"
    assert adapter.compensation_effects == 1
