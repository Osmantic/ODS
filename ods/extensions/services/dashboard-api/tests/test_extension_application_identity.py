"""Exact contract tests for extension_application_identity.

Covers: happy path, deterministic label order/digest, each binding mismatch,
wrong operation/service, noop rejection, optional Compose sentinel, missing/
malformed/unknown ODS label, unrelated labels ignored, digest tamper, type
confusion/bools, no value leakage, repeated output equality, and source-level
proving the module is not imported by ods-host-agent.py or production runtime
and executor remains None.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_application_identity as app_id  # noqa: E402, RUF100
import extension_lifecycle_plan as lifecycle_plan  # noqa: E402, RUF100
import extension_lifecycle_work as lifecycle_work  # noqa: E402, RUF100

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
DEFINITION_SHA = "sha256:" + "3" * 64
COMPOSE_SHA = "sha256:" + "4" * 64
SERVICE_ID = "documents"
VERSION = "1.2.3"
ACTION = "install"


def _definition(service_id: str, compose: str | None = COMPOSE_SHA) -> dict:
    return {
        "id": service_id,
        "serviceType": "docker",
        "manifestSchemaVersion": "ods.services.v2",
        "version": VERSION,
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": "2.0.0", "maximum": "3.0.0"},
        "definitionSha256": DEFINITION_SHA,
        "composeSha256": compose,
        "definitionSource": "library",
        "composeFile": "compose.yaml" if compose else None,
        "dependsOn": [],
        "provides": [],
        "requires": [],
        "conflicts": [],
        "requirements": {},
        "estimates": {},
        "configuration": [],
        "artifacts": {
            "images": [
                {
                    "reference": f"example.invalid/{service_id}:{VERSION}",
                    "digest": "sha256:" + "5" * 64,
                    "downloadBytes": 123,
                }
            ],
            "builds": [],
        },
        "resources": {},
        "lifecycle": {},
        "data": [],
        "trust": {},
        "support": {},
    }


def _transaction(state: str, definitions: list[dict]) -> dict:
    return {
        "transactionId": TRANSACTION_ID,
        "state": state,
        "approval": {
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "approvedBy": "owner",
        },
        "envelope": {
            "planHash": PLAN_HASH,
            "plan": {
                "selectedServices": [d["id"] for d in definitions],
                "operations": [
                    {"serviceId": d["id"], "action": ACTION} for d in definitions
                ],
                "definitions": definitions,
            },
        },
    }


def _command(operation_key: str, service_ids: list[str], payload: dict) -> lifecycle_work.LifecycleWorkCommand:
    unsigned = {
        "schema": lifecycle_work.REQUEST_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": operation_key,
        "serviceIds": service_ids,
        "payload": payload,
    }
    request = {
        **unsigned,
        "requestHash": hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    return lifecycle_work.parse_lifecycle_work_request(request)


def _bound_command(
    operation_key: str = f"apply:{SERVICE_ID}",
    service_ids: list[str] | None = None,
    payload: dict | None = None,
    compose: str | None = COMPOSE_SHA,
) -> lifecycle_work.LifecycleWorkCommand:
    if service_ids is None:
        service_ids = [SERVICE_ID]
    if payload is None:
        payload = {"operation": {"serviceId": service_ids[0], "action": ACTION}}
    definitions = [_definition(s, compose) for s in service_ids]
    tx = _transaction("applying", definitions)
    cmd = _command(operation_key, service_ids, payload)
    return lifecycle_plan.bind_lifecycle_plan(cmd, tx)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_produces_identity():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)

    assert isinstance(identity, app_id.ApplicationIdentity)
    assert identity.service_id == SERVICE_ID
    assert identity.version == VERSION
    assert identity.action == ACTION
    assert identity.transaction_id == TRANSACTION_ID
    assert identity.plan_sha256 == PLAN_HASH
    assert identity.request_sha256 == cmd.request_hash
    assert identity.definition_sha256 == DEFINITION_SHA
    assert identity.compose_sha256 == COMPOSE_SHA
    assert isinstance(identity.identity_sha256, str)
    assert len(identity.identity_sha256) == 64

    # Identity is frozen
    with pytest.raises(FrozenInstanceError):
        identity.service_id = "x"  # type: ignore


def test_produce_rejects_lone_surrogate_version_with_stable_error():
    cmd = _bound_command()
    material = cmd.plan_material
    definition = replace(material.definitions[0], version="\ud800")
    tampered = replace(
        cmd,
        plan_material=replace(material, definitions=(definition,)),
    )

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(tampered)
    assert exc.value.code == "version-required"


def test_happy_path_identity_is_deterministic():
    cmd = _bound_command()
    id1 = app_id.produce_application_identity(cmd)
    id2 = app_id.produce_application_identity(cmd)
    assert id1 is not id2
    assert id1 == id2
    assert id1.identity_sha256 == id2.identity_sha256


def test_read_only_recovery_identity_does_not_authorize_apply() -> None:
    applying = _bound_command()
    parsed = _command(
        f"apply:{SERVICE_ID}",
        [SERVICE_ID],
        {"operation": {"serviceId": SERVICE_ID, "action": ACTION}},
    )
    recovering = lifecycle_plan.bind_lifecycle_plan(
        parsed,
        _transaction("reconciling", [_definition(SERVICE_ID)]),
        read_only_observation=True,
    )
    with pytest.raises(app_id.ApplicationIdentityError) as caught:
        app_id.produce_application_identity(recovering)
    assert caught.value.code == "plan-material-invalid"
    assert app_id.produce_application_observation_identity(
        recovering
    ) == app_id.produce_application_identity(applying)
    wrong_state = replace(
        applying, plan_material=replace(applying.plan_material, state="verifying")
    )
    with pytest.raises(app_id.ApplicationIdentityError) as caught:
        app_id.produce_application_observation_identity(wrong_state)
    assert caught.value.code == "plan-material-invalid"
    with pytest.raises(app_id.ApplicationIdentityError):
        app_id._verify_apply_command(
            recovering, read_only_observation=1  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Label encoding / decoding round-trip
# ---------------------------------------------------------------------------


def test_identity_labels_roundtrip():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    labels = app_id.identity_labels(identity)

    # Verify namespace
    for key in labels:
        assert key.startswith(app_id.LABEL_NAMESPACE + ".")

    # Round-trip parse
    parsed = app_id.parse_observed_labels(labels)
    assert parsed == identity


def test_label_keys_are_deterministic_and_sorted():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    labels = app_id.identity_labels(identity)
    keys = list(labels.keys())
    assert keys == sorted(keys)


def test_identity_sha256_computed_over_canonical_bytes():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)

    expected_payload = {
        "action": identity.action,
        "composeSha256": identity.compose_sha256,
        "definitionSha256": identity.definition_sha256,
        "planSha256": identity.plan_sha256,
        "requestSha256": identity.request_sha256,
        "serviceId": identity.service_id,
        "transactionId": identity.transaction_id,
        "version": identity.version,
    }
    canonical = (
        json.dumps(
            expected_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    expected = hashlib.sha256(canonical).hexdigest()
    assert identity.identity_sha256 == expected


# ---------------------------------------------------------------------------
# Each binding mismatch
# ---------------------------------------------------------------------------


def test_transaction_id_mismatch():
    cmd = _bound_command()
    material = cmd.plan_material
    fake_material = replace(material, transaction_id="txn-" + "9" * 24)
    cmd = replace(cmd, plan_material=fake_material)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(cmd)
    assert exc.value.code == "transaction-id-mismatch"


def test_plan_hash_mismatch():
    cmd = _bound_command()
    material = cmd.plan_material
    fake_material = replace(material, plan_hash="9" * 64)
    cmd = replace(cmd, plan_material=fake_material)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(cmd)
    assert exc.value.code == "plan-hash-mismatch"


def test_request_hash_must_remain_canonical():
    cmd = replace(_bound_command(), request_hash="not-a-hash")
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(cmd)
    assert exc.value.code == "request-hash-invalid"


def test_plan_material_schema_and_state_are_reproved():
    cmd = _bound_command()
    invalid_schema = replace(
        cmd, plan_material=replace(cmd.plan_material, schema="wrong")
    )
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(invalid_schema)
    assert exc.value.code == "plan-material-invalid"

    invalid_state = replace(
        cmd, plan_material=replace(cmd.plan_material, state="verifying")
    )
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(invalid_state)
    assert exc.value.code == "plan-material-invalid"


def test_payload_action_must_match_bound_plan_operation():
    cmd = _bound_command()
    changed = replace(cmd.plan_material.operations[0], action="update")
    cmd = replace(
        cmd,
        plan_material=replace(cmd.plan_material, operations=(changed,)),
    )
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(cmd)
    assert exc.value.code == "operation-binding-mismatch"


def test_definition_binding_required_no_match():
    """If no definition matches the service ID, fail."""
    # Build a valid command for "documents" but tamper definitions to remove it
    cmd = _bound_command()
    material = cmd.plan_material
    new_defs = tuple(d for d in material.definitions if d.service_id != SERVICE_ID)
    tampered_material = replace(material, definitions=new_defs)
    tampered_cmd = replace(cmd, plan_material=tampered_material)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(tampered_cmd)
    assert exc.value.code == "definition-binding-required"


def test_definition_binding_required_duplicate():
    """If two definitions match the service ID, fail."""
    cmd = _bound_command()
    material = cmd.plan_material
    first_def = material.definitions[0]
    # Add a duplicate by appending a copy
    new_defs = material.definitions + (first_def,)
    tampered_material = replace(material, definitions=new_defs)
    tampered_cmd = replace(cmd, plan_material=tampered_material)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(tampered_cmd)
    assert exc.value.code == "definition-binding-required"


# ---------------------------------------------------------------------------
# Wrong operation / service
# ---------------------------------------------------------------------------


def test_wrong_operation_reserve():
    raw = _command("reserve:documents", [SERVICE_ID], {"operation": {"serviceId": SERVICE_ID, "action": ACTION}})
    defs = [_definition(SERVICE_ID)]
    defs[0]["resources"] = {"hostPorts": [], "exclusive": []}
    tx = _transaction("reserved", defs)
    bound = lifecycle_plan.bind_lifecycle_plan(raw, tx)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(bound)
    assert exc.value.code == "operation-must-be-apply-service"


def test_wrong_operation_download():
    raw = _command("download-and-verify", [SERVICE_ID], {"operations": [{"serviceId": SERVICE_ID, "action": ACTION}]})
    defs = [_definition(SERVICE_ID)]
    tx = _transaction("downloading", defs)
    bound = lifecycle_plan.bind_lifecycle_plan(raw, tx)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(bound)
    assert exc.value.code == "operation-must-be-apply-service"


def test_wrong_service_suffix():
    """Operation key service suffix must match service_ids[0]."""
    # Build a valid bound command, then tamper service_ids to not match the suffix
    cmd = _bound_command()
    tampered = replace(cmd, service_ids=("other-suffix",))

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(tampered)
    assert exc.value.code == "operation-must-be-apply-service"


def test_noop_action_rejected():
    """noop is not a valid action in the identity contract."""
    # Manually set action to noop in the payload
    # But noop won't pass the lifecycle work validator for apply:<svc>
    # We need to manually set up the command
    cmd = _bound_command()
    # Tamper the payload to have action=noop
    new_payload = {"operation": {"serviceId": SERVICE_ID, "action": "noop"}}
    tampered = replace(cmd, payload=new_payload)
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(tampered)
    assert exc.value.code == "action-invalid"


# ---------------------------------------------------------------------------
# Optional Compose sentinel
# ---------------------------------------------------------------------------


def test_optional_compose_sentinel_when_absent():
    """When composeSha256 is None in the definition, use sentinel."""
    cmd = _bound_command(compose=None)
    identity = app_id.produce_application_identity(cmd)
    assert identity.compose_sha256 == app_id.COMPOSE_DIGEST_SENTINEL
    assert identity.compose_sha256 == "absent"


def test_compose_sentinel_in_labels():
    """Sentinel appears correctly in labels and round-trips."""
    cmd = _bound_command(compose=None)
    identity = app_id.produce_application_identity(cmd)
    labels = app_id.identity_labels(identity)
    expected_key = app_id.LABEL_NAMESPACE + ".compose_sha256"
    assert labels[expected_key] == "absent"

    parsed = app_id.parse_observed_labels(labels)
    assert parsed.compose_sha256 == "absent"


# ---------------------------------------------------------------------------
# Label parsing: missing / malformed / unknown ODS label
# ---------------------------------------------------------------------------


def _make_labels(compose: str | None = COMPOSE_SHA) -> dict[str, str]:
    cmd = _bound_command(compose=compose)
    identity = app_id.produce_application_identity(cmd)
    return app_id.identity_labels(identity)


def test_missing_label_transaction_id():
    labels = _make_labels()
    del labels[app_id.LABEL_NAMESPACE + ".transaction_id"]
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert "missing-label" in exc.value.code


def test_missing_label_service_id():
    labels = _make_labels()
    del labels[app_id.LABEL_NAMESPACE + ".service_id"]
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert "missing-label" in exc.value.code


def test_malformed_label_invalid_plan_sha256():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".plan_sha256"] = "not-a-hash"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_malformed_label_invalid_request_sha256():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".request_sha256"] = "not-a-hash"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_malformed_label_invalid_transaction_id():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".transaction_id"] = "not-a-transaction"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_version_control_character_is_rejected():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".version"] = "1.2.3\nsecret"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_observed_lone_surrogate_version_has_stable_value_free_error():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".version"] = "\udfff"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_malformed_label_invalid_definition_sha256():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".definition_sha256"] = "sha256:abc"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_malformed_label_invalid_compose_sha256():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".compose_sha256"] = "invalid"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "label-value-invalid"


def test_unknown_ods_label_rejected():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".unknown_field"] = "x"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "unknown-ods-label"


def test_unrelated_labels_ignored():
    labels = _make_labels()
    labels["com.docker.compose.project"] = "my-project"
    labels["com.docker.compose.service"] = "documents"
    labels["custom.label"] = "value"
    identity = app_id.parse_observed_labels(labels)
    assert identity.service_id == SERVICE_ID


def test_empty_labels():
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels({})
    assert "missing-label" in exc.value.code


# ---------------------------------------------------------------------------
# Digest tamper
# ---------------------------------------------------------------------------


def test_digest_tamper_detected():
    labels = _make_labels()
    # Flip one bit in the identity SHA
    old = labels[app_id.LABEL_NAMESPACE + ".identity_sha256"]
    tampered = old[:1] + ("1" if old[0] == "0" else "0") + old[2:]
    labels[app_id.LABEL_NAMESPACE + ".identity_sha256"] = tampered
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "identity-digest-mismatch"


def test_payload_field_tamper_detected():
    labels = _make_labels()
    # Change version but not the identity hash
    labels[app_id.LABEL_NAMESPACE + ".version"] = "9.9.9"
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "identity-digest-mismatch"


def test_identity_labels_revalidates_frozen_value():
    identity = app_id.produce_application_identity(_bound_command())
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.identity_labels(replace(identity, version="9.9.9"))
    assert exc.value.code == "identity-digest-mismatch"


# ---------------------------------------------------------------------------
# Type confusion / booleans
# ---------------------------------------------------------------------------


def test_labels_not_mapping():
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels([])  # type: ignore
    assert exc.value.code == "labels-must-be-mapping"


def test_labels_value_is_bool():
    labels = _make_labels()
    labels[app_id.LABEL_NAMESPACE + ".version"] = True  # type: ignore
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "labels-must-be-string"


def test_labels_key_is_bool():
    labels = _make_labels()
    del labels[app_id.LABEL_NAMESPACE + ".version"]
    labels[True] = "1.2.3"  # type: ignore
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.parse_observed_labels(labels)
    assert exc.value.code == "labels-must-be-string"


def test_command_not_lifecycle_work_command():
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity("not a command")  # type: ignore
    assert exc.value.code == "not-a-command"


def test_command_no_plan_material():
    cmd = _command("apply:documents", [SERVICE_ID], {"operation": {"serviceId": SERVICE_ID, "action": ACTION}})
    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(cmd)
    assert exc.value.code == "plan-material-required"


# ---------------------------------------------------------------------------
# No value leakage
# ---------------------------------------------------------------------------


def test_no_value_leakage_in_exception():
    """Exception text must never contain supplied values."""
    raw = _command("reserve:documents", [SERVICE_ID], {"operation": {"serviceId": SERVICE_ID, "action": ACTION}})
    defs = [_definition(SERVICE_ID)]
    defs[0]["resources"] = {"hostPorts": [], "exclusive": []}
    tx = _transaction("reserved", defs)
    bound = lifecycle_plan.bind_lifecycle_plan(raw, tx)

    with pytest.raises(app_id.ApplicationIdentityError) as exc:
        app_id.produce_application_identity(bound)

    err_str = str(exc.value)
    assert TRANSACTION_ID not in err_str
    assert PLAN_HASH not in err_str
    assert SERVICE_ID not in err_str
    assert VERSION not in err_str
    assert DEFINITION_SHA not in err_str
    assert exc.value.code == "operation-must-be-apply-service"


def test_error_code_is_fixed_value_free():
    """All error codes must be fixed strings without values."""
    for bad_input in [
        lambda: app_id.produce_application_identity(None),  # type: ignore
        lambda: app_id.parse_observed_labels("x"),  # type: ignore
        lambda: app_id.parse_observed_labels({}),
        lambda: app_id.parse_observed_labels({app_id.LABEL_NAMESPACE + ".service_id": "x"}),
    ]:
        try:
            bad_input()
        except app_id.ApplicationIdentityError as exc:
            code = exc.code
            # Code must be a simple identifier-like string
            assert isinstance(code, str)
            assert code.isascii()
            # Must not contain user values
            assert TRANSACTION_ID not in code
            assert SERVICE_ID not in code
            assert PLAN_HASH not in code


# ---------------------------------------------------------------------------
# Repeated output equality
# ---------------------------------------------------------------------------


def test_repeated_produce_equality():
    cmd = _bound_command()
    results = [app_id.produce_application_identity(cmd) for _ in range(5)]
    for i in range(1, len(results)):
        assert results[i] == results[0]
        assert results[i].identity_sha256 == results[0].identity_sha256


def test_repeated_label_equality():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    labels_list = [app_id.identity_labels(identity) for _ in range(5)]
    for i in range(1, len(labels_list)):
        assert labels_list[i] == labels_list[0]


def test_repeated_parse_equality():
    labels = _make_labels()
    parsed_list = [app_id.parse_observed_labels(labels) for _ in range(5)]
    for i in range(1, len(parsed_list)):
        assert parsed_list[i] == parsed_list[0]


# ---------------------------------------------------------------------------
# Source-level: module not imported by production paths
# ---------------------------------------------------------------------------


def test_not_imported_by_host_agent():
    """Prove ods-host-agent.py does not import extension_application_identity."""
    agent_path = BIN_DIR / "ods-host-agent.py"
    source = agent_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    import_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_names.add(node.module.split(".")[0])

    assert "extension_application_identity" not in import_names


def test_no_runtime_effects_on_import():
    """Module must be pure stdlib with no effect primitives."""
    module_path = BIN_DIR / "extension_application_identity.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".", 1)[0]
                imports.add(name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    allowed_imports = {
        "__future__",
        "extension_lifecycle_plan",
        "extension_lifecycle_work",
        "hashlib",
        "json",
        "re",
        "dataclasses",
        "typing",
        "hmac",
    }
    assert imports <= allowed_imports
    assert imports.isdisjoint(
        {"os", "pathlib", "requests", "shutil", "socket", "subprocess",
         "urllib", "sys", "env", "tempfile"}
    )
    assert called_names.isdisjoint(
        {"open", "exec", "eval", "compile", "system", "remove", "unlink",
         "Popen", "run", "check_output"}
    )


def test_executor_remains_none():
    """Production runtime executor must remain None (module is dormant)."""
    import inspect
    # Verify the module has no dispatcher/executor attribute
    assert not hasattr(app_id, "dispatcher")
    assert not hasattr(app_id, "executor")
    assert not hasattr(app_id, "execute")
    assert not hasattr(app_id, "dispatch")
    # The module only has function-level public callables (not types/dataclasses)
    function_public = [
        name for name in app_id.__all__
        if inspect.isfunction(getattr(app_id, name, None))
    ]
    assert function_public == [
        "identity_labels",
        "parse_observed_labels",
        "produce_application_identity",
        "produce_application_observation_identity",
    ]
