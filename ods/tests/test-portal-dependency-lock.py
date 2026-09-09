#!/usr/bin/env python3
"""Adversarial contract tests for the Portal dependency lock."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import jsonschema
except ModuleNotFoundError:  # The GitHub integration lane installs this dependency.
    jsonschema = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-portal-dependency-lock.py"
LOCK_PATH = ROOT / "config" / "portal-dependency-lock.json"


def load_module():
    spec = importlib.util.spec_from_file_location("check_portal_dependency_lock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def expect_rejected(module, value, message: str) -> None:
    try:
        module.validate(value)
    except module.LockError:
        return
    raise AssertionError(message)


def test_repo_lock_passes() -> None:
    module = load_module()
    assert module.check() == []


def test_duplicate_json_keys_are_rejected() -> None:
    module = load_module()
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "lock.json"
        path.write_text('{"schemaVersion":1,"schemaVersion":1}\n', encoding="utf-8")
        errors = module.check(path)
    assert errors and "duplicate JSON key" in errors[0]


def test_private_head_substitution_is_rejected() -> None:
    module = load_module()
    value = module.load_lock()
    value["portalCore"]["sourceCommit"] = "0" * 40
    expect_rejected(module, value, "a substituted private source head was accepted")


def test_source_pr_mutation_or_drop_is_rejected() -> None:
    module = load_module()
    value = module.load_lock()
    value["sourceInputs"][0]["disposition"] = "dropped"
    expect_rejected(module, value, "a dropped superseded source PR was accepted")


def test_missing_blocker_is_rejected() -> None:
    module = load_module()
    value = module.load_lock()
    value["qualification"]["blockers"].pop()
    expect_rejected(module, value, "an incomplete blocker set was accepted")


def test_false_release_green_is_rejected() -> None:
    module = load_module()
    value = module.load_lock()
    value["qualification"]["state"] = "release-green"
    value["qualification"]["releaseGreen"] = True
    expect_rejected(module, value, "an unsigned, unqualified source lock claimed release green")


def test_json_boolean_integer_substitutions_are_rejected() -> None:
    module = load_module()
    substitutions = [
        (("schemaVersion",), True),
        (("product", "genericPlaceholder"), 1),
        (("portalCore", "candidateArtifact", "immutable"), 0),
        (("qualification", "releaseGreen"), 0),
    ]
    for path, replacement in substitutions:
        value = module.load_lock()
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
        expect_rejected(module, value, f"boolean/integer substitution at {path} was accepted")


def test_hermes_manifest_substitution_is_rejected() -> None:
    module = load_module()
    value = module.load_lock()
    value["upstreamHermes"]["oci"]["manifests"][0]["manifestDigest"] = "sha256:" + "0" * 64
    expect_rejected(module, value, "a substituted Hermes platform manifest was accepted")


def test_display_name_cannot_become_security_identity() -> None:
    module = load_module()
    value = module.load_lock()
    value["product"]["securityIdentitySource"] = "hermes-profile-display-name"
    expect_rejected(module, value, "a presentation name was accepted as a security identity")


def test_schema_is_valid_json_and_tracks_the_lock() -> None:
    schema = json.loads(
        (ROOT / "config" / "portal-dependency-lock-v1.schema.json").read_text(encoding="utf-8")
    )
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if jsonschema is not None:
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
        validator_class(schema, format_checker=jsonschema.FormatChecker()).validate(lock)
    assert schema["properties"]["$schema"]["const"] == lock["$schema"]
    assert schema["properties"]["schemaVersion"]["const"] == lock["schemaVersion"]
    assert schema["$defs"]["portalCore"]["properties"]["runtimeActivated"]["const"] is False
    assert schema["$defs"]["portalCore"]["properties"]["evidenceFiles"]["minItems"] == 5
    assert schema["$defs"]["portalCore"]["properties"]["evidenceFiles"]["maxItems"] == 5
    assert schema["$defs"]["qualification"]["properties"]["releaseGreen"]["const"] is False


def main() -> int:
    tests = [
        test_repo_lock_passes,
        test_duplicate_json_keys_are_rejected,
        test_private_head_substitution_is_rejected,
        test_source_pr_mutation_or_drop_is_rejected,
        test_missing_blocker_is_rejected,
        test_false_release_green_is_rejected,
        test_json_boolean_integer_substitutions_are_rejected,
        test_hermes_manifest_substitution_is_rejected,
        test_display_name_cannot_become_security_identity,
        test_schema_is_valid_json_and_tracks_the_lock,
    ]
    for test in tests:
        test()
    print("[PASS] Portal dependency lock tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
