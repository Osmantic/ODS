"""Contract tests for the vendored pixel_provider_runtime_public module.

`pixel_provider_runtime_public.py` is a byte-for-byte copy of
`bin/pixel_provider/public.py`, vendored into dashboard-api so the service can
validate provider runtime envelopes without importing the host-side package.
The host-side copy is covered by `ods/tests/pixel_inference/`; the vendored
copy had no coverage at all.

These tests pin two things:

- The vendored file stays identical to the host source, so the producer and
  consumer validators cannot silently drift apart.
- The vendored module's own import path works and enforces the contract.
"""

from pathlib import Path

import pytest

import pixel_provider_runtime_public as contract

DASHBOARD_API_DIR = Path(__file__).resolve().parents[1]
HOST_SOURCE = DASHBOARD_API_DIR.parents[2] / "bin" / "pixel_provider" / "public.py"
VENDORED_SOURCE = DASHBOARD_API_DIR / "pixel_provider_runtime_public.py"

REV = "a" * 64
TXN = "123e4567-e89b-42d3-a456-426614174000"
TIMESTAMP = "2026-09-17T12:00:00Z"

KEYS = {"schemaVersion", "status", "revision", "providerRevision", "binding",
        "pending", "registrationVerified", "transportVerified",
        "lastVerifiedAt", "reason"}


def _binding(revision=4):
    return {
        "schemaVersion": 1,
        "activationId": TXN,
        "revision": revision,
        "allowCloud": False,
    }


def _runtime(status, **overrides):
    doc = {
        "schemaVersion": 1,
        "status": status,
        "revision": REV,
        "providerRevision": 4,
        "binding": None,
        "pending": False,
        "registrationVerified": False,
        "transportVerified": False,
        "lastVerifiedAt": None,
        "reason": None,
    }
    doc.update(overrides)
    return doc


def test_vendored_copy_matches_host_source():
    assert VENDORED_SOURCE.read_bytes() == HOST_SOURCE.read_bytes(), (
        "pixel_provider_runtime_public.py is a vendored copy of "
        "bin/pixel_provider/public.py and must stay identical; sync the two "
        "files or drop the vendored copy."
    )


def test_unavailable_envelope():
    doc = contract.unavailable("runtime-busy")
    assert doc["status"] == "unavailable"
    assert doc["reason"] == "runtime-busy"
    assert all(doc[k] is None for k in KEYS
               - {"schemaVersion", "status", "reason"})
    with pytest.raises(ValueError):
        contract.unavailable("not-an-allowlisted-reason")


def test_safe_reason_falls_back():
    assert contract.safe_reason("model-lifecycle-busy") == "model-lifecycle-busy"
    assert contract.safe_reason("invented") == "provider-controller-unavailable"
    assert contract.safe_reason(42) == "provider-controller-unavailable"
    assert contract.safe_reason("invented", fallback=None) is None


@pytest.mark.parametrize("operation", ["apply", "deactivate", "recover"])
def test_normalize_change_accepts_operations(operation):
    change = {"operation": operation, "revision": REV, "providerRevision": 7}
    assert contract.normalize_change(change) == change


@pytest.mark.parametrize("change", [
    {"operation": "delete", "revision": REV, "providerRevision": 7},
    {"operation": "apply", "revision": "short", "providerRevision": 7},
    {"operation": "apply", "revision": REV, "providerRevision": True},
    {"operation": "apply", "revision": REV},
    {"operation": "apply", "revision": REV, "providerRevision": 7, "x": 1},
    ["apply", REV, 7],
])
def test_normalize_change_rejects(change):
    with pytest.raises(ValueError):
        contract.normalize_change(change)


def test_normalize_binding_round_trip():
    binding = _binding()
    assert contract.normalize_binding(binding) == binding
    assert contract.normalize_binding(None) is None
    for bad in ({"schemaVersion": 2, **{k: v for k, v in binding.items()
                                       if k != "schemaVersion"}},
                dict(binding, activationId="not-a-uuid"),
                dict(binding, activationId=TXN.upper()),
                dict(binding, revision=2**53),
                dict(binding, extra=1)):
        with pytest.raises(ValueError):
            contract.normalize_binding(bad)


def test_normalize_runtime_state_machine():
    unavailable = {k: None for k in KEYS}
    unavailable.update({"schemaVersion": 1, "status": "unavailable",
                        "reason": "runtime-busy"})
    assert contract.normalize_runtime(unavailable) == unavailable

    assert contract.normalize_runtime(_runtime("pending", pending=True))
    assert contract.normalize_runtime(_runtime("not-applied"))

    applied = _runtime("applied", binding=_binding(4),
                       registrationVerified=True, lastVerifiedAt=TIMESTAMP)
    assert contract.normalize_runtime(applied)["status"] == "applied"

    saved = _runtime("saved-changes", binding=_binding(2),
                     registrationVerified=True, lastVerifiedAt=TIMESTAMP)
    assert contract.normalize_runtime(saved)["status"] == "saved-changes"

    inactive = _runtime("inactive", registrationVerified=True,
                        lastVerifiedAt=TIMESTAMP)
    assert contract.normalize_runtime(inactive)["status"] == "inactive"


@pytest.mark.parametrize("doc", [
    _runtime("applied", binding=_binding(3), registrationVerified=True,
             lastVerifiedAt=TIMESTAMP),           # stale binding on applied
    _runtime("saved-changes", binding=_binding(4), registrationVerified=True,
             lastVerifiedAt=TIMESTAMP),           # synced binding on saved
    _runtime("inactive", binding=_binding(4), registrationVerified=True,
             lastVerifiedAt=TIMESTAMP),           # binding on inactive
    _runtime("pending", pending=False),           # flag mismatch
    _runtime("pending", pending=True, binding=_binding(4)),
    _runtime("applied", binding=_binding(4), registrationVerified=False,
             lastVerifiedAt=TIMESTAMP),
    _runtime("applied", binding=_binding(4), registrationVerified=True,
             lastVerifiedAt="not-a-timestamp"),
    _runtime("applied", binding=_binding(4), registrationVerified=True,
             lastVerifiedAt=TIMESTAMP, transportVerified=True),
    _runtime("applied", binding=_binding(4), registrationVerified=True,
             lastVerifiedAt=TIMESTAMP, reason="runtime-busy"),
    _runtime("mystery-state"),
])
def test_normalize_runtime_rejects_inconsistent_states(doc):
    with pytest.raises(ValueError):
        contract.normalize_runtime(doc)


def test_normalize_runtime_returns_detached_copy():
    doc = _runtime("applied", binding=_binding(4), registrationVerified=True,
                   lastVerifiedAt=TIMESTAMP)
    result = contract.normalize_runtime(doc)
    result["binding"]["revision"] = 999
    assert doc["binding"]["revision"] == 4


def test_from_controller_injects_envelope_fields():
    doc = {k: v for k, v in _runtime("pending", pending=True).items()
           if k not in ("schemaVersion", "reason")}
    result = contract.from_controller(doc)
    assert result["schemaVersion"] == 1 and result["reason"] is None
    doc["extra"] = True
    with pytest.raises(ValueError):
        contract.from_controller(doc)


def test_normalize_outcome_matches_request():
    request = {"operation": "apply", "revision": REV, "providerRevision": 4}
    outcome = {"outcome": "applied", "binding": _binding(4),
               "registrationVerified": True, "transportVerified": False}
    assert contract.normalize_outcome(outcome, request)["outcome"] == "applied"

    # Rolled-back is only meaningful for recover.
    rolled = {"outcome": "rolled-back", "binding": None,
              "registrationVerified": True, "transportVerified": False}
    with pytest.raises(ValueError):
        contract.normalize_outcome(rolled, request)
    assert contract.normalize_outcome(
        rolled, {**request, "operation": "recover"})["outcome"] == "rolled-back"

    # Deactivate must not return a binding; apply must return the matching one.
    deactivate = {**request, "operation": "deactivate"}
    assert contract.normalize_outcome({**outcome, "binding": None}, deactivate)
    with pytest.raises(ValueError):
        contract.normalize_outcome(outcome, deactivate)
    with pytest.raises(ValueError):
        contract.normalize_outcome({**outcome, "binding": _binding(9)}, request)
