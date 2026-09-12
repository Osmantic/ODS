from __future__ import annotations

import copy
import hashlib

import assistant_first_planner as planner
from extension_planning_contract import computed_catalog_revision
from extension_transaction_runtime import CurrentInputsProvenanceVerifier
from test_plan_provenance import HOST_STATE, POLICY, authorize, catalog_entry, intent


def build_current_envelope(entries=None, state=None, policy=None):
    entries = entries or [catalog_entry("notes")]
    state = state or copy.deepcopy(HOST_STATE)
    policy = policy or copy.deepcopy(POLICY)
    envelope = authorize(
        intent(requestedServices=["notes"]),
        entries=entries,
        state=state,
        policy=policy,
    )
    return envelope, entries, state, policy


def verifier(entries, state, policy):
    return CurrentInputsProvenanceVerifier(
        catalog=lambda: (entries, computed_catalog_revision(entries)),
        observed_state=lambda: state,
        policy=lambda: policy,
    )


def test_current_inputs_verifier_accepts_only_exact_current_envelope():
    envelope, entries, state, policy = build_current_envelope()
    check = verifier(entries, state, policy)
    assert check.verify(envelope["planHash"], envelope) is True


def test_current_inputs_verifier_rejects_catalog_state_and_policy_drift():
    envelope, entries, state, policy = build_current_envelope()

    drifted_catalog = copy.deepcopy(entries)
    drifted_catalog[0]["planning"]["version"] = "1.2.4"
    assert verifier(drifted_catalog, state, policy).verify(
        envelope["planHash"], envelope
    ) is False

    drifted_state = copy.deepcopy(state)
    drifted_state["available"]["diskBytes"] -= 1
    assert verifier(entries, drifted_state, policy).verify(
        envelope["planHash"], envelope
    ) is False

    drifted_policy = copy.deepcopy(policy)
    drifted_policy["allowExperimental"] = True
    assert verifier(entries, state, drifted_policy).verify(
        envelope["planHash"], envelope
    ) is False


def test_current_inputs_verifier_rejects_selected_definition_tampering():
    envelope, entries, state, policy = build_current_envelope()
    tampered = copy.deepcopy(envelope)
    tampered["plan"]["definitions"][0]["definitionSha256"] = "f" * 64
    tampered_hash = hashlib.sha256(
        planner.canonical_json_bytes(tampered["plan"])
    ).hexdigest()
    tampered["planHash"] = tampered_hash
    tampered["planId"] = "plan-" + tampered_hash[:24]

    assert verifier(entries, state, policy).verify(tampered_hash, tampered) is False


def test_current_inputs_verifier_fails_closed_on_provider_shape():
    envelope, _entries, state, policy = build_current_envelope()
    check = CurrentInputsProvenanceVerifier(
        catalog=lambda: [],
        observed_state=lambda: state,
        policy=lambda: policy,
    )
    assert check.verify(envelope["planHash"], envelope) is False
