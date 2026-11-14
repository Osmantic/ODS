import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "scripts" / "bootstrap-upgrade.sh"


def _extract_dispatch():
    text = SOURCE.read_text(encoding='utf-8')
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
    with script.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(body)
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    proc = subprocess.run(
        [os.environ.get('ODS_TEST_BASH', 'bash'), script.as_posix(), install.as_posix(), uname, str(rc)],
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
    assert Path(args[1]) == install
    assert args[2] == "full-model.gguf"
    assert args[3] == "full-llm"
    assert args[4] == "131072"


def test_managed_darwin_install_path_with_spaces(tmp_path):
    spaced = tmp_path / "has space"
    spaced.mkdir()
    proc, trace, args, install = _run(spaced, "Darwin", 0)
    assert proc.returncode == 0, proc.stderr
    assert Path(args[1]) == install
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


@pytest.mark.parametrize("install_name,previous", [
    ("install", "old.gguf"),
    ("My ODS", "old.gguf"),
    ("install", "old model.gguf"),
    ("My ODS", "old model.gguf"),
    ("My ODS", ""),
])
def test_legacy_native_failure_retains_previous_model_path(tmp_path, install_name, previous):
    install = tmp_path / install_name
    for directory in ("models", "data", "bin", "scripts", "installers/macos/lib"):
        (install / directory).mkdir(parents=True, exist_ok=True)
    (install / "scripts/macos-bootstrap-promote.py").write_text("fixture")
    (install / "models/full-model.gguf").write_text("full")
    if previous:
        (install / "models" / previous).write_text("previous")
    (install / ".env").write_text("GGUF_FILE=" + previous + "\nOLLAMA_PORT=8080\n")
    # Impossible PIDs and shell stubs prevent any signal or live service call.
    (install / "data/.llama-server.pid").write_text("999999991\n")
    binary = install / "bin/llama-server"
    binary.write_text("#!/bin/sh\nexit 99\n")
    binary.chmod(0o755)
    helper = install / "installers/macos/lib/native-llama-service.sh"
    helper.write_text('''#!/bin/sh
set -eu
printf '%s\\0' "$@" >> "$2/launches"
printf '\\n' >> "$2/launches"
printf '999999992\\n' > "$4"
''')
    overrides = r'''
read_env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | head -1; }
promote_full_model_env() {
    printf 'GGUF_FILE=%s\nCTX_SIZE=32768\nOLLAMA_PORT=8080\n' "$FULL_GGUF_FILE" > "$ENV_FILE"
}
kill() { return 0; }
sleep() { return 0; }
curl() { return 22; }
ps() {
    case " $* " in
        *' args= '*)
            if [[ -n "$PREVIOUS_MODEL" ]]; then
                printf '%s --model %s --port 8080\n' "$INSTALL_DIR/bin/llama-server" "$MODELS_DIR/$PREVIOUS_MODEL"
            fi ;;
        *) printf 'llama-server\n' ;;
    esac
}
'''
    body = HARNESS.replace('set -eu', 'set -u', 1)
    body = body.replace('__DISPATCH__', 'PREVIOUS_MODEL="$4"\n' + overrides + _extract_dispatch())
    script = tmp_path / "legacy-harness.sh"
    script.write_text(body)
    proc = subprocess.run(
        [os.environ.get('ODS_TEST_BASH', 'bash'), str(script), str(install), "Darwin", "10", previous],
        capture_output=True, text=True, env=dict(os.environ, PATH="/usr/bin:/bin"), timeout=15,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    launches = [row.split(b'\0')[:-1] for row in (install / "launches").read_bytes().splitlines()]
    assert len(launches) == (2 if previous else 1), proc.stdout + proc.stderr
    first = [value.decode() for value in launches[0]]
    assert first[first.index('--model') + 1] == str(install / "models/full-model.gguf")
    if previous:
        rollback = [value.decode() for value in launches[1]]
        assert rollback[rollback.index('--model') + 1] == str(install / "models" / previous)
        assert (install / "models" / previous).read_text() == "previous"
    assert "GGUF_FILE=full-model.gguf\n" in (install / ".env").read_text()
    assert "STATUS failed" in (install / "trace").read_text()
