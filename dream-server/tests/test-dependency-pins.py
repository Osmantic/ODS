#!/usr/bin/env python3
"""Unit tests for dependency pin enforcement."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-dependency-pins.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_dependency_pins", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repo_dependency_lock_passes() -> None:
    module = load_module()
    errors = module.check()
    assert errors == [], "\n".join(errors)


def test_unallowlisted_latest_is_rejected() -> None:
    module = load_module()
    lock = {
        "entries": [],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="compose.yaml",
        line=3,
        raw="postgres:latest",
        value="postgres:latest",
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert any("latest tag requires allow_latest" in error for error in errors)


def test_variable_refs_must_be_documented() -> None:
    module = load_module()
    lock = {
        "entries": [
            {
                "path": "compose.yaml",
                "value": "postgres:17.9-alpine",
            }
        ],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="compose.yaml",
        line=3,
        raw="${POSTGRES_IMAGE:-postgres:17.9-alpine}",
        value="postgres:17.9-alpine",
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert any("variable image ref is not documented" in error for error in errors)


def test_ephemeral_sha_tag_is_rejected() -> None:
    """Ephemeral sha-<commit> tags must be rejected (issue #1544)."""
    module = load_module()
    lock = {
        "entries": [
            {
                "id": "test.sha-pin",
                "path": "compose.yaml",
                "value": "nousresearch/hermes-agent:sha-dd0923bb89ed2dd56f82cb63656a1323f6f42e6f",
            }
        ],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="compose.yaml",
        line=6,
        raw="nousresearch/hermes-agent:sha-dd0923bb89ed2dd56f82cb63656a1323f6f42e6f",
        value="nousresearch/hermes-agent:sha-dd0923bb89ed2dd56f82cb63656a1323f6f42e6f",
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert any("ephemeral sha-<commit> tag" in error for error in errors)


def test_digest_ref_is_accepted() -> None:
    """Full @sha256: digest refs must be accepted (they are immutable, not ephemeral)."""
    module = load_module()
    lock = {
        "entries": [
            {
                "id": "test.digest",
                "path": "compose.yaml",
                "value": "myrepo/myimage:slim-latest@sha256:6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e",
            }
        ],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="compose.yaml",
        line=3,
        raw="myrepo/myimage:slim-latest@sha256:6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e",
        value="myrepo/myimage:slim-latest@sha256:6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e",
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert not any("ephemeral" in e for e in errors), f"Digest ref should not be rejected: {errors}"


def test_local_allowlisted_sha_is_accepted() -> None:
    """Locally-built images with sha-style tags must be accepted when allowlisted."""
    module = load_module()
    lock = {
        "entries": [],
        "allow_latest": [],
        "allow_local_images": [
            {
                "path": "compose.yaml",
                "value": "dream-custom:sha-abc1234567",
                "reason": "Local image built from adjacent Dockerfile.",
            }
        ],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="compose.yaml",
        line=3,
        raw="dream-custom:sha-abc1234567",
        value="dream-custom:sha-abc1234567",
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert not any("ephemeral" in e for e in errors), f"Local allowlisted sha ref should not be rejected: {errors}"


def main() -> int:
    tests = [
        test_repo_dependency_lock_passes,
        test_unallowlisted_latest_is_rejected,
        test_variable_refs_must_be_documented,
        test_ephemeral_sha_tag_is_rejected,
        test_digest_ref_is_accepted,
        test_local_allowlisted_sha_is_accepted,
    ]
    for test in tests:
        test()
    print("[PASS] dependency pin tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
