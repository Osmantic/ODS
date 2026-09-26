#!/usr/bin/env python3
"""Unit tests for dependency pin enforcement."""

from __future__ import annotations

import importlib.util
import json
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


def test_langfuse_minio_images_are_pinned_source_builds() -> None:
    import re
    import yaml

    lock = json.loads((ROOT / "config" / "dependency-lock.json").read_text())
    by_entry = {entry["id"]: entry for entry in lock["entries"]}
    by_id = {ident: entry["value"] for ident, entry in by_entry.items()}
    allowed = {(item["path"], item["value"]) for item in lock["allow_local_images"]}
    fragment = ROOT / "extensions/services/langfuse/compose.yaml.disabled"
    services = yaml.safe_load(fragment.read_text())["services"]
    pins = [
        ("langfuse-minio", "minio", "MINIO", "langfuse.minio", "RELEASE.2025-09-07T16-13-09Z", "07c3a429bfed433e49018cb0f78a52145d4bedeb"),
        ("langfuse-minio-init", "mc", "MC", "langfuse.minio-client", "RELEASE.2025-08-13T08-35-41Z", "7394ce0dd2a80935aded936b09fa12cbb3cb8096"),
    ]
    module = load_module()
    for service, component, prefix, ident, release, commit in pins:
        image = f"ods-langfuse-{component}:{release}"
        assert services[service]["image"] == by_id[ident] == image
        assert services[service]["build"] == {"context": "./extensions/services/langfuse", "dockerfile": f"Dockerfile.{component}"}
        assert ("extensions/services/langfuse/compose.yaml.disabled", image) in allowed
        dockerfile = fragment.parent / f"Dockerfile.{component}"
        source = dockerfile.read_text()
        args = dict(re.findall(r"^ARG ([A-Z_]+)=(.+)$", source, re.MULTILINE))
        assert by_entry[ident]["source"] == {"repository": f"https://github.com/minio/{component}", "tag": release, "commit": commit}
        assert args[f"{prefix}_RELEASE"] == release
        assert args[f"{prefix}_COMMIT"] == by_entry[ident]["source"]["commit"] == commit
        assert f'test "${{actual}}" = "${{{prefix}_COMMIT}}"' in source
        assert f'{prefix}_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${{{prefix}_RELEASE#RELEASE.}}"' in source
        drift = module.ImageRef(path="extensions/services/langfuse/compose.yaml.disabled", line=1,
            raw=f"ods-langfuse-{component}:unrecorded", value=f"ods-langfuse-{component}:unrecorded", source="compose image")
        assert module.validate_refs([drift], lock), "An unrecorded local-image tag must fail closed"
    assert services["langfuse-minio"]["command"] == 'server /data --console-address ":9001"'
    assert services["langfuse-minio"]["healthcheck"]["test"] == ["CMD-SHELL", "curl -sf http://127.0.0.1:9000/minio/health/live"]
    assert "mc mb local/langfuse-events --ignore-existing" in services["langfuse-minio-init"]["command"][-1]


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


def _llama_ref_errors(module, image: str) -> list[str]:
    lock = {
        "entries": [{"path": "docker-compose.nvidia.yml", "value": image}],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="docker-compose.nvidia.yml",
        line=8,
        raw=image,
        value=image,
        source="compose image",
    )
    return module.validate_refs([ref], lock)


def test_llama_cpp_images_require_tag_and_digest() -> None:
    module = load_module()
    digest = "@sha256:" + ("a" * 64)
    for image in (
        "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014",
        "ghcr.io/ggml-org/llama.cpp" + digest,
        "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014@sha256:abc",
    ):
        errors = _llama_ref_errors(module, image)
        assert any("must be pinned by tag and @sha256 digest" in error for error in errors), image

    pinned = "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014" + digest
    assert _llama_ref_errors(module, pinned) == []

    # Other repositories keep the tag-or-digest policy.
    other = "ghcr.io/open-webui/open-webui:v0.7.2"
    lock = {
        "entries": [{"path": "docker-compose.base.yml", "value": other}],
        "allow_latest": [],
        "allow_local_images": [],
        "allow_variable_refs": [],
    }
    ref = module.ImageRef(
        path="docker-compose.base.yml", line=1, raw=other, value=other, source="compose image"
    )
    assert module.validate_refs([ref], lock) == []


def test_repo_llama_cpp_pins_carry_digests() -> None:
    module = load_module()
    refs = [
        ref
        for ref in module.discover_image_refs()
        if module._image_repository(ref.value) == "ghcr.io/ggml-org/llama.cpp"
    ]
    assert refs, "expected llama.cpp images in the shipped compose files"
    for ref in refs:
        assert module.DIGEST_RE.search(ref.value), f"{ref.path}:{ref.line}: {ref.value}"


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
        test_langfuse_minio_images_are_pinned_source_builds,
        test_unallowlisted_latest_is_rejected,
        test_variable_refs_must_be_documented,
        test_ephemeral_sha_tags_are_rejected,
        test_ephemeral_sha256_length_tags_are_rejected,
        test_sha256_digest_pins_are_allowed,
        test_llama_cpp_images_require_tag_and_digest,
        test_repo_llama_cpp_pins_carry_digests,
        test_extension_library_sha_tags_are_rejected,
    ]
    for test in tests:
        test()
    print("[PASS] dependency pin tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
