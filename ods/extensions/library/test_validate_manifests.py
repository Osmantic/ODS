"""Tests for validate-manifests.py.

The script is loaded by path because its filename contains a hyphen, and its
directory constants are rebound onto a temp tree so main() can be driven
end-to-end without touching the real services directory.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "validate-manifests.py"

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "port": {"type": "integer"}},
    "required": ["name"],
}


def load_script():
    spec = importlib.util.spec_from_file_location("validate_manifests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def validator(tmp_path):
    """The script, pointed at an empty temp services tree."""
    module = load_script()
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA))

    services = tmp_path / "services"
    services.mkdir()

    module.MANIFEST_FILE = tmp_path / "manifest.json"  # absent -> local fallback
    module.LOCAL_SCHEMA_PATH = schema_file
    module.SERVICES_DIR = services
    return module


def add_service(validator, name, body):
    service = validator.SERVICES_DIR / name
    service.mkdir(parents=True, exist_ok=True)
    manifest = service / "manifest.yaml"
    manifest.write_text(body)
    return manifest


def run(validator, capsys):
    """Run main(), returning (exit_code, stdout)."""
    with pytest.raises(SystemExit) as exc:
        validator.main()
    return exc.value.code, capsys.readouterr().out


# --- the size limit ---------------------------------------------------------

def test_oversized_manifest_is_rejected(validator, capsys):
    body = "name: big\npadding: " + "x" * (validator.MAX_MANIFEST_SIZE + 1)
    add_service(validator, "big", body)

    code, out = run(validator, capsys)

    assert code == 1
    assert "FAIL  big" in out
    assert "too large" in out


def test_oversized_manifest_is_not_parsed(validator, capsys):
    """The size check must short-circuit before the YAML parser sees the file."""
    # Valid by size-check standards only: parsing this would raise a YAML error,
    # so a "too large" message proves the parser was never reached.
    body = "name: big\nbroken: {unclosed\n" + "x" * validator.MAX_MANIFEST_SIZE
    add_service(validator, "big", body)

    code, out = run(validator, capsys)

    assert code == 1
    assert "too large" in out
    assert "YAML parse error" not in out


def test_manifest_just_under_limit_is_still_validated(validator, capsys):
    padding = "x" * (validator.MAX_MANIFEST_SIZE - 200)
    add_service(validator, "snug", f"name: snug\npadding: {padding}\n")

    code, out = run(validator, capsys)

    assert code == 0
    assert "PASS  snug" in out


def test_limit_is_one_megabyte(validator):
    assert validator.MAX_MANIFEST_SIZE == 1024 * 1024


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_manifest_is_reported_not_raised(validator, capsys):
    manifest = add_service(validator, "locked", "name: locked\n")
    manifest.chmod(0o000)
    try:
        code, out = run(validator, capsys)
    finally:
        manifest.chmod(0o644)

    assert code == 1
    assert "FAIL  locked" in out
    assert "File read error" in out


def test_one_oversized_manifest_does_not_stop_the_others(validator, capsys):
    add_service(validator, "big", "name: big\np: " + "x" * validator.MAX_MANIFEST_SIZE)
    add_service(validator, "small", "name: small\n")

    code, out = run(validator, capsys)

    assert code == 1
    assert "FAIL  big" in out
    assert "PASS  small" in out
    assert "Total: 2  Passed: 1  Failed: 1" in out


# --- pre-existing behaviour that must survive the change --------------------

def test_valid_manifest_passes(validator, capsys):
    add_service(validator, "good", "name: good\nport: 8080\n")

    code, out = run(validator, capsys)

    assert code == 0
    assert "PASS  good" in out


def test_malformed_yaml_is_reported(validator, capsys):
    add_service(validator, "broken", "name: broken\nbad: {unclosed\n")

    code, out = run(validator, capsys)

    assert code == 1
    assert "YAML parse error" in out


def test_empty_manifest_is_reported(validator, capsys):
    add_service(validator, "blank", "")

    code, out = run(validator, capsys)

    assert code == 1
    assert "Empty manifest" in out


def test_schema_violation_names_the_field(validator, capsys):
    add_service(validator, "typo", "name: typo\nport: not-a-number\n")

    code, out = run(validator, capsys)

    assert code == 1
    assert "FAIL  typo" in out
    assert "port" in out


def test_no_manifests_exits_zero(validator, capsys):
    code, out = run(validator, capsys)

    assert code == 0
    assert "No manifest files found" in out


def test_missing_services_directory_exits_two(validator, capsys):
    validator.SERVICES_DIR = validator.SERVICES_DIR / "nope"

    code, out = run(validator, capsys)

    assert code == 2
    assert "Services directory not found" in out
