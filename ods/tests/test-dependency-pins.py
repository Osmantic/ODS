#!/usr/bin/env python3
"""Unit tests for dependency pin enforcement."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


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


def test_ephemeral_sha_tags_are_rejected() -> None:
    module = load_module()
    lock = {
        "entries": [
            {
                "path": "extensions/services/hermes/compose.yaml",
                "value": "nousresearch/hermes-agent:sha-dd0923bb89ed2dd56f82cb63656a1323f6f42e6f",
            }
        ],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="extensions/services/hermes/compose.yaml",
        line=6,
        raw="nousresearch/hermes-agent:sha-dd0923bb89ed2dd56f82cb63656a1323f6f42e6f",
        value="nousresearch/hermes-agent:sha-dd0923bb89ed2dd56f82cb63656a1323f6f42e6f",
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert any("ephemeral sha-* image tags are not release-stable" in error for error in errors)


def test_ephemeral_sha256_length_tags_are_rejected() -> None:
    module = load_module()
    image = "nousresearch/hermes-agent:sha-" + ("a" * 64)
    lock = {
        "entries": [
            {
                "path": "extensions/services/hermes/compose.yaml",
                "value": image,
            }
        ],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="extensions/services/hermes/compose.yaml",
        line=6,
        raw=image,
        value=image,
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert any("ephemeral sha-* image tags are not release-stable" in error for error in errors)


def test_sha256_digest_pins_are_allowed() -> None:
    module = load_module()
    image = (
        "nousresearch/hermes-agent@sha256:"
        "6e399abf4ff587822b0ef0df11f36088fb928e17ac61556fe89beb68d48c378e"
    )
    lock = {
        "entries": [
            {
                "path": "extensions/services/hermes/compose.yaml",
                "value": image,
            }
        ],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="extensions/services/hermes/compose.yaml",
        line=6,
        raw=image,
        value=image,
        source="compose image",
    )
    errors = module.validate_refs([ref], lock)
    assert errors == [], "\n".join(errors)


def test_extension_library_sha_tags_are_rejected() -> None:
    module = load_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "config"
        service = root / "extensions" / "library" / "services" / "example"
        config.mkdir()
        service.mkdir(parents=True)
        lock_path = config / "dependency-lock.json"
        lock_path.write_text(
            (
                '{"version": 1, "entries": [], "allow_latest": [], '
                '"allow_local_images": [], "allow_variable_refs": []}\n'
            ),
            encoding="utf-8",
        )
        (service / "compose.yaml").write_text(
            "services:\n  app:\n    image: example/runtime:sha-1234567890abcdef\n",
            encoding="utf-8",
        )

        errors = module.check(lock_path, root)

    assert any("ephemeral sha-* image tags are not release-stable" in error for error in errors)
    assert any(
        "extensions/library/services/example/compose.yaml:3" in error for error in errors
    )


def main() -> int:
    tests = [
        test_repo_dependency_lock_passes,
        test_unallowlisted_latest_is_rejected,
        test_variable_refs_must_be_documented,
        test_ephemeral_sha_tags_are_rejected,
        test_ephemeral_sha256_length_tags_are_rejected,
        test_sha256_digest_pins_are_allowed,
        test_extension_library_sha_tags_are_rejected,
    ]
    for test in tests:
        test()
    print("[PASS] dependency pin tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
