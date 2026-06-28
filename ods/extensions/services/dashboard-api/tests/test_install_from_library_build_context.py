"""Tests for build.context rewriting during library extension install."""

import logging
import os
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from routers.extensions import _rewrite_build_context


def _write(path: Path, doc: dict) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False)


def _read(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def test_rewrites_relative_dot_context(tmp_path, caplog):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/audiocraft")
    _write(compose, {
        "services": {
            "audiocraft": {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "image": "audiocraft:local",
            },
        },
    })

    with caplog.at_level(logging.INFO, logger="routers.extensions"):
        _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["audiocraft"]["build"]["context"] == str(final_dir.resolve())
    assert data["services"]["audiocraft"]["build"]["dockerfile"] == "Dockerfile"
    assert any("Rewrote build context" in rec.message for rec in caplog.records)


def test_rewrites_other_relative_context(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {
            "foo": {
                "build": {"context": "./subdir", "dockerfile": "Dockerfile"},
            },
        },
    })

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["foo"]["build"]["context"] == str(
        (final_dir / "subdir").resolve()
    )


def test_short_form_string_build(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {
            "foo": {"build": "."},
        },
    })

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["foo"]["build"] == {"context": str(final_dir.resolve())}


def test_short_form_preserves_relative_suffix(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {
            "foo": {"build": "docker"},
        },
    })

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["foo"]["build"] == {
        "context": str((final_dir / "docker").resolve())
    }


def test_missing_context_key_is_added(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {
            "foo": {"build": {"dockerfile": "Dockerfile"}},
        },
    })

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["foo"]["build"]["context"] == str(final_dir.resolve())
    assert data["services"]["foo"]["build"]["dockerfile"] == "Dockerfile"


def test_idempotent_absolute_context_left_alone(tmp_path, caplog):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    existing_abs = "/some/other/absolute/path"
    _write(compose, {
        "services": {
            "foo": {"build": {"context": existing_abs}},
        },
    })
    mtime_before = os.path.getmtime(compose)

    with caplog.at_level(logging.INFO, logger="routers.extensions"):
        _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["foo"]["build"]["context"] == existing_abs
    assert os.path.getmtime(compose) == mtime_before
    assert not any("Rewrote build context" in rec.message for rec in caplog.records)


def test_idempotent_absolute_short_form(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {"foo": {"build": "/already/absolute"}},
    })

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["foo"]["build"] == "/already/absolute"


def test_no_build_field_no_change(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {
            "foo": {"image": "nginx:latest"},
        },
    })
    mtime_before = os.path.getmtime(compose)

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert "build" not in data["services"]["foo"]
    assert os.path.getmtime(compose) == mtime_before


def test_multiple_services_mixed(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/multi")
    _write(compose, {
        "services": {
            "needs_rewrite": {"build": {"context": ".", "dockerfile": "Dockerfile"}},
            "already_abs": {"build": {"context": "/keep/me"}},
            "no_build": {"image": "nginx"},
        },
    })

    _rewrite_build_context(compose, final_dir)

    data = _read(compose)
    assert data["services"]["needs_rewrite"]["build"]["context"] == str(final_dir.resolve())
    assert data["services"]["already_abs"]["build"]["context"] == "/keep/me"
    assert "build" not in data["services"]["no_build"]


def test_rejects_context_that_escapes_extension_dir(tmp_path):
    compose = tmp_path / "compose.yaml"
    final_dir = Path("/var/lib/ods/user-extensions/foo")
    _write(compose, {
        "services": {
            "foo": {"build": {"context": "../bar"}},
        },
    })

    with pytest.raises(HTTPException) as exc:
        _rewrite_build_context(compose, final_dir)

    assert exc.value.status_code == 400
    assert "escapes the extension directory" in exc.value.detail


def test_invalid_yaml_root_no_crash(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text("just a string\n")
    final_dir = Path("/var/lib/ods/user-extensions/foo")

    # Should not raise — top-level isn't a dict
    _rewrite_build_context(compose, final_dir)


def test_malformed_yaml_propagates(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text("services:\n  foo: [unterminated\n")
    final_dir = Path("/var/lib/ods/user-extensions/foo")

    with pytest.raises(yaml.YAMLError):
        _rewrite_build_context(compose, final_dir)
