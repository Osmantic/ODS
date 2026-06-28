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

# Write a `docker` stub at $STUB_BIN/docker that prints $1 to stderr (real
# Docker writes the OCI failure to stderr) and exits with $2. The probe in
# preflight-fs.sh redirects 2>&1 and greps the combined stream, so writing to
# stderr exercises the same code path real Docker Desktop hits.
_make_docker_stub() {
    local message="$1"
    local exit_code="$2"
    # NOTE: unquoted heredoc — $message is shell-expanded into the stub at
    # write time; pass only literal strings (no $, backticks, or backslashes)
    # or the substitution will run in this shell rather than the stub.
    cat > "$STUB_BIN/docker" <<MOCK
#!/bin/bash
echo "$message" >&2
exit $exit_code
MOCK
    chmod +x "$STUB_BIN/docker"
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
    assert_output --partial "Mounts denied"
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
    assert_output --partial "not shared from the host"
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
    assert_output --partial "Filesharing"
}

@test "docker-desktop sharing: unrelated docker error does not flip OK" {
    # A failure that is NOT a sharing issue (e.g. the daemon is down) must
    # not be reported as a sharing failure — DOCKER_SHARE_OK stays true so
    # the installer can surface the real error elsewhere instead of blaming
    # the file-sharing allowlist.
    _make_docker_stub "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" 1

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$PREFLIGHT_FS_SH"'"
        test_docker_desktop_sharing "'"$INSTALL_DIR"'"
        echo "OK=$DOCKER_SHARE_OK"
        echo "ERR=$DOCKER_SHARE_ERR"
    '
    assert_success
    assert_output --partial "OK=true"
}
