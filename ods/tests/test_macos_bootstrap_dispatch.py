import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "scripts" / "bootstrap-upgrade.sh"


def _extract_dispatch():
    text = SOURCE.read_text()
    marker = "promote_managed_macos_bootstrap() {"
    start = text.index(marker if marker in text else "# Download bytes stay outside the lifecycle lock.")
    end = text.index("# \u2500\u2500 Phase 5c:")
    return text[start:end]


HARNESS = r'''
set -eu
INSTALL_DIR="$1"
FAKE_UNAME="$2"
FAKE_RC="$3"

uname() { printf '%s\n' "$FAKE_UNAME"; }
python3() { printf '%s\n' "$@" > "$INSTALL_DIR/args"; return "$FAKE_RC"; }
log() { printf 'LOG %s\n' "$*" >> "$INSTALL_DIR/trace"; }
write_status() { printf 'STATUS %s\n' "$*" >> "$INSTALL_DIR/trace"; }
fail() { printf 'FAIL %s\n' "$*" >> "$INSTALL_DIR/trace"; exit 1; }
is_windows_bash() { return 1; }
read_env_value() { printf '%s\n' ""; }
refresh_lemonade_after_bootstrap_cleanup() { return 0; }
acquire_model_lifecycle_lock() { printf 'LOCK\n' >> "$INSTALL_DIR/trace"; return 0; }
acquire_bootstrap_pixel_model_transaction() { printf 'PIXELGATE\n' >> "$INSTALL_DIR/trace"; return 0; }
acquire_model_router_swap_gate() { printf 'ROUTERGATE\n' >> "$INSTALL_DIR/trace"; return 0; }
promote_full_model_env() { printf 'ENV %s\n' "$*" >> "$INSTALL_DIR/trace"; exit 42; }

MODELS_DIR="$INSTALL_DIR/models"
ENV_FILE="$INSTALL_DIR/.env"
MODELS_INI="$INSTALL_DIR/models.ini"
FULL_GGUF_FILE="full-model.gguf"
FULL_LLM_MODEL="full-llm"
FULL_MAX_CONTEXT="131072"
BOOTSTRAP_GGUF_FILE="bootstrap.gguf"
TOTAL_BYTES=100
DOCKER_CMD=""
DOCKER_COMPOSE_CMD=""

__DISPATCH__
'''


def _run(tmp_path, uname, rc, helper=True, symlink=False, docker=""):
    install = tmp_path / "install"
    install.mkdir()
    (install / "scripts").mkdir()
    (install / "models").mkdir()
    (install / "models" / "bootstrap.gguf").write_text("x")
    (install / "models" / "full-model.gguf").write_text("x")
    helper_path = install / "scripts" / "macos-bootstrap-promote.py"
    if symlink:
        target = install / "real-helper.py"
        target.write_text("x")
        helper_path.symlink_to(target)
    elif helper:
        helper_path.write_text("x")
    script = tmp_path / "harness.sh"
    body = HARNESS.replace("__DISPATCH__", _extract_dispatch())
    body = body.replace('DOCKER_CMD=""', 'DOCKER_CMD="%s"' % docker)
    script.write_text(body)
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    proc = subprocess.run(
        ["bash", str(script), str(install), uname, str(rc)],
        capture_output=True, text=True, env=env, timeout=15,
    )
    trace = (install / "trace").read_text() if (install / "trace").exists() else ""
    args = (install / "args").read_text().splitlines() if (install / "args").exists() else []
    return proc, trace, args, install


def test_managed_darwin_success_cleans_bootstrap(tmp_path):
    proc, trace, args, install = _run(tmp_path, "Darwin", 0)
    assert proc.returncode == 0, proc.stderr
    assert "LOCK" not in trace
    assert "ENV" not in trace
    assert not (install / "models" / "bootstrap.gguf").exists()
    assert (install / "models" / "full-model.gguf").exists()
    assert len(args) == 5
    assert args[0].endswith("macos-bootstrap-promote.py")
    assert args[1] == str(install)
    assert args[2] == "full-model.gguf"
    assert args[3] == "full-llm"
    assert args[4] == "131072"


def test_managed_darwin_install_path_with_spaces(tmp_path):
    spaced = tmp_path / "has space"
    spaced.mkdir()
    proc, trace, args, install = _run(spaced, "Darwin", 0)
    assert proc.returncode == 0, proc.stderr
    assert args[1] == str(install)
    assert " " in args[1]


@pytest.mark.parametrize("rc", [1, 124])
def test_helper_failure_keeps_bootstrap(tmp_path, rc):
    proc, trace, args, install = _run(tmp_path, "Darwin", rc)
    assert proc.returncode != 0
    assert "FAIL" in trace
    assert "LOCK" not in trace
    assert "ENV" not in trace
    assert (install / "models" / "bootstrap.gguf").exists()


def test_helper_missing_fails_closed(tmp_path):
    proc, trace, args, install = _run(tmp_path, "Darwin", 0, helper=False)
    assert proc.returncode != 0
    assert "FAIL" in trace
    assert "LOCK" not in trace
    assert (install / "models" / "bootstrap.gguf").exists()


def test_helper_symlink_fails_closed(tmp_path):
    proc, trace, args, install = _run(tmp_path, "Darwin", 0, symlink=True)
    assert proc.returncode != 0
    assert "FAIL" in trace
    assert "LOCK" not in trace
    assert (install / "models" / "bootstrap.gguf").exists()


def test_unmanaged_darwin_falls_through_to_legacy(tmp_path):
    proc, trace, args, install = _run(tmp_path, "Darwin", 10)
    assert proc.returncode == 42
    assert "ENV" in trace
    assert "LOCK" in trace
    assert (install / "models" / "bootstrap.gguf").exists()


def test_linux_uname_skips_python(tmp_path):
    proc, trace, args, install = _run(tmp_path, "Linux", 0, docker="")
    assert proc.returncode == 42
    assert args == []
    assert "ENV" in trace


def test_windows_uname_skips_python(tmp_path):
    proc, trace, args, install = _run(tmp_path, "MINGW64_NT", 0)
    assert proc.returncode == 42
    assert args == []
    assert "ENV" in trace


def test_darwin_with_docker_sentinel_still_skips_docker(tmp_path):
    proc, trace, args, install = _run(tmp_path, "Darwin", 0, docker="bogus-sentinel")
    assert proc.returncode == 0, proc.stderr
    assert "LOCK" not in trace
    assert not (install / "models" / "bootstrap.gguf").exists()
