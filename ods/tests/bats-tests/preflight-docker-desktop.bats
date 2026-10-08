#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/macos/lib/preflight-fs.sh::test_docker_desktop_sharing
# ============================================================================
# Strategy: stub the `docker` binary in PATH so each test deterministically
# emits the message + exit-code combination that real Docker Desktop produces
# when the install dir is (or is not) on the file-sharing allowlist. Source
# the helper, invoke `test_docker_desktop_sharing`, and assert on
# DOCKER_SHARE_OK / DOCKER_SHARE_ERR.
#
# Issue #505 (Docker-Desktop side): the bind-mount probe in preflight-fs.sh
# is a behavioral check — a regression that, e.g., flipped the grep pattern
# or reversed the OK/ERR semantics would currently slip through unit tests.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export INSTALL_DIR="$BATS_TEST_TMPDIR/install-target"
    mkdir -p "$INSTALL_DIR"

    export STUB_BIN="$BATS_TEST_TMPDIR/stub-bin"
    mkdir -p "$STUB_BIN"

    # The helper file lives in ods/installers/macos/lib/.
    PREFLIGHT_FS_SH="$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"
    export PREFLIGHT_FS_SH
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/install-target" "$BATS_TEST_TMPDIR/stub-bin"
}

# Keep fixture data out of shell source so quotes/control characters stay literal.
_make_docker_stub() {
    export DOCKER_STUB_MESSAGE="$1"
    export DOCKER_STUB_STATUS="$2"
    cat > "$STUB_BIN/docker" <<'MOCK'
#!/bin/bash
printf '%s\n' "$DOCKER_STUB_MESSAGE" >&2
exit "$DOCKER_STUB_STATUS"
MOCK
    chmod +x "$STUB_BIN/docker"
}

_run_probe() {
    run bash -euo pipefail -c '
        export PATH="$STUB_BIN:$PATH"
        source "$PREFLIGHT_FS_SH"
        test_docker_desktop_sharing "$INSTALL_DIR"
        printf "OK=%s\nREASON=%s\nERR=%s\n" "$DOCKER_SHARE_OK" "${DOCKER_SHARE_REASON:-}" "$DOCKER_SHARE_ERR"
    '
}

_run_installer_sharing_check() {
    # Exercise the actual installer block, not a copy of its branch logic.
    local block="$BATS_TEST_TMPDIR/sharing-check.sh"
    awk '/^test_docker_desktop_sharing "\$INSTALL_DIR"$/ { printing=1 }
         printing { print }
         printing && /^ai_ok "Docker Desktop file sharing OK"$/ { complete=1; exit }
         END { if (!complete) exit 1 }' \
        "$BATS_TEST_DIRNAME/../../installers/macos/install-macos.sh" > "$block"
    [[ -s "$block" ]]
    run bash -euo pipefail -c '
        export PATH="$STUB_BIN:$PATH"
        source "$PREFLIGHT_FS_SH"
        ai_err() { printf "%s\n" "$*"; }
        ai() { printf "%s\n" "$*"; }
        ai_ok() { printf "%s\n" "$*"; }
        source "$1"
    ' bash "$block"
}

# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@test "docker-desktop sharing: 'Mounts denied' is reported as not OK" {
    _make_docker_stub "Error response from daemon: Mounts denied: The path /private/tmp/install-target is not shared from the host and is not known to Docker." 1

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$PREFLIGHT_FS_SH"'"
        test_docker_desktop_sharing "'"$INSTALL_DIR"'"
        echo "OK=$DOCKER_SHARE_OK"
        echo "ERR=$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "File Sharing"
}

@test "docker-desktop sharing: 'not shared from the host' is reported as not OK" {
    _make_docker_stub "docker: Error response from daemon: error while creating mount source path '/Volumes/X/ods': mkdir /Volumes/X/ods: not shared from the host and is not known to Docker." 1

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$PREFLIGHT_FS_SH"'"
        test_docker_desktop_sharing "'"$INSTALL_DIR"'"
        echo "OK=$DOCKER_SHARE_OK"
        echo "ERR=$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "File Sharing"
}

@test "docker-desktop sharing: clean run reports OK" {
    # docker exits 0 and emits nothing meaningful — the alpine container
    # successfully bind-mounted the probe path, then `true` returned.
    _make_docker_stub "" 0

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$PREFLIGHT_FS_SH"'"
        test_docker_desktop_sharing "'"$INSTALL_DIR"'"
        echo "OK=$DOCKER_SHARE_OK"
        echo "ERR=$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output --partial "OK=true"
    refute_output --partial "OK=false"
}

@test "docker-desktop sharing: missing docker CLI is reported as not OK" {
    # No `docker` binary in PATH at all — make sure the helper degrades
    # gracefully instead of letting `command -v docker` propagate an error
    # under `set -euo pipefail`.
    rm -f "$STUB_BIN/docker"

    run bash -c '
        # Strip the system PATH so docker cannot be found anywhere.
        export PATH="'"$STUB_BIN"'"
        source "'"$PREFLIGHT_FS_SH"'"
        test_docker_desktop_sharing "'"$INSTALL_DIR"'"
        echo "OK=$DOCKER_SHARE_OK"
        echo "ERR=$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "docker CLI not found"
}

@test "docker-desktop sharing: case-insensitive 'file sharing' phrase is detected" {
    # Older Docker Desktop builds emitted "Filesharing" / "File sharing"
    # phrasing rather than the "Mounts denied" / "not shared" idiom. The
    # helper greps with -iE and an alternation that includes those — make
    # sure the regex still flips OK=false.
    _make_docker_stub "ERROR: Filesharing is not configured for the path /Users/test/ods" 1

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$PREFLIGHT_FS_SH"'"
        test_docker_desktop_sharing "'"$INSTALL_DIR"'"
        echo "OK=$DOCKER_SHARE_OK"
        echo "ERR=$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "File Sharing"
}

@test "docker-desktop sharing: unrelated daemon failure fails closed under errexit" {
    _make_docker_stub "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" 125
    _run_probe
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "REASON=probe_failed"
    assert_output --partial "exit 125"
    refute_output --partial "File Sharing"
}

@test "docker-desktop sharing: registry DNS failure fails closed" {
    _make_docker_stub 'Get "https://registry-1.docker.io/v2/": dial tcp: lookup registry-1.docker.io: no such host' 125
    _run_probe
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "REASON=probe_failed"
    refute_output --partial "File Sharing"
}

@test "docker-desktop sharing: empty nonzero output fails closed with exit status" {
    _make_docker_stub "" 42
    _run_probe
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "exit 42"
}

@test "docker-desktop sharing: raw multiline errors and terminal controls are not reflected" {
    _make_docker_stub $'https://user:secret@private.example/\n/private/customer\n\033[31mPRIVATE-ERROR\033[0m' 125
    _run_probe
    assert_success
    assert_output --partial "OK=false"
    refute_output --partial "secret"
    refute_output --partial "customer"
    refute_output --partial "PRIVATE-ERROR"
    refute_output --partial $'\033'
}

@test "docker-desktop sharing: mount denial diagnostics do not reflect raw output" {
    _make_docker_stub $'Mounts denied: /private/customer\nhttps://user:secret@private.example/\033[31m' 125
    _run_probe
    assert_success
    assert_output --partial "OK=false"
    assert_output --partial "REASON=denied"
    assert_output --partial "File Sharing"
    refute_output --partial "secret"
    refute_output --partial "customer"
    refute_output --partial $'\033'
}

@test "docker-desktop sharing: a successful retry clears all previous failure state" {
    _make_docker_stub "Mounts denied" 125
    run bash -euo pipefail -c '
        export PATH="$STUB_BIN:$PATH"
        source "$PREFLIGHT_FS_SH"
        test_docker_desktop_sharing "$INSTALL_DIR"
        [[ "$DOCKER_SHARE_OK" == false ]]
        export DOCKER_STUB_STATUS=0 DOCKER_STUB_MESSAGE=""
        test_docker_desktop_sharing "$INSTALL_DIR"
        printf "OK=%s\nREASON=%s\nERR=%s\n" "$DOCKER_SHARE_OK" "${DOCKER_SHARE_REASON:-}" "$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output $'OK=true\nREASON=ok\nERR='
}

@test "docker-desktop sharing: successful output mentioning file sharing is still success" {
    _make_docker_stub "File sharing probe completed" 0
    _run_probe
    assert_success
    assert_output --partial "OK=true"
    assert_output --partial "REASON=ok"
}

@test "docker-desktop installer: generic probe failure stops without allowlist advice" {
    _make_docker_stub "Cannot connect to the Docker daemon. PRIVATE-ERROR" 125
    _run_installer_sharing_check
    assert_failure 1
    assert_output --partial "Docker bind-mount probe failed (exit 125)"
    refute_output --partial "File Sharing"
    refute_output --partial "file sharing OK"
    refute_output --partial "PRIVATE-ERROR"
}

@test "docker-desktop installer: confirmed sharing denial stops with allowlist advice" {
    _make_docker_stub "Mounts denied: /private/customer" 125
    _run_installer_sharing_check
    assert_failure 1
    assert_output --partial "Settings > Resources > File Sharing"
    refute_output --partial "file sharing OK"
    refute_output --partial "/private/customer"
}

@test "docker-desktop installer: successful bind-mount probe continues" {
    _make_docker_stub "" 0
    _run_installer_sharing_check
    assert_success
    assert_output "Docker Desktop file sharing OK"
}
