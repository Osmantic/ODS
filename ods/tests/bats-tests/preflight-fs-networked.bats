#!/usr/bin/env bats
# ============================================================================
# BATS tests for networked-filesystem detection in the preflight phase.
# ============================================================================
# Covers:
#   * macOS:  installers/macos/lib/preflight-fs.sh::test_install_dir_filesystem
#   * Linux:  installers/phases/01-preflight.sh::check_install_dir_filesystem
#
# Strategy: stub `stat` via PATH so each test deterministically reports a
# specific filesystem type, then source the relevant helper and assert on
# INSTALL_FS_NETWORKED / warn output / fatal exit behavior.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export INSTALL_DIR="$BATS_TEST_TMPDIR/install-target"
    mkdir -p "$INSTALL_DIR"

    # PATH stub directory for `stat` (and friends, if needed).
    export STUB_BIN="$BATS_TEST_TMPDIR/stub-bin"
    mkdir -p "$STUB_BIN"

    # Stub `diskutil` to exit non-zero on every call so the macOS
    # personality-refinement branch in preflight-fs.sh is deterministically
    # bypassed; INSTALL_FS_TYPE then stays equal to whatever the stat stub
    # returned. Linux tests don't invoke diskutil so this stub is harmless.
    cat > "$STUB_BIN/diskutil" <<'MOCK'
#!/bin/bash
exit 1
MOCK
    chmod +x "$STUB_BIN/diskutil"
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/install-target" "$BATS_TEST_TMPDIR/stub-bin"
}

# ---------------------------------------------------------------------------
# Helpers: write a `stat` stub that prints the requested filesystem type.
# ---------------------------------------------------------------------------

# BSD stat stub: macOS preflight-fs.sh calls `stat -f %T <path>`.
_make_bsd_stat_stub() {
    local fs_type="$1"
    cat > "$STUB_BIN/stat" <<MOCK
#!/bin/bash
# Match BSD-style \`stat -f %T <path>\`.
if [[ "\$1" == "-f" && "\$2" == "%T" ]]; then
    echo "$fs_type"
    exit 0
fi
exit 0
MOCK
    chmod +x "$STUB_BIN/stat"
}

# GNU stat stub: Linux 01-preflight.sh calls `stat -fc %T <path>`.
_make_gnu_stat_stub() {
    local fs_type="$1"
    cat > "$STUB_BIN/stat" <<MOCK
#!/bin/bash
# Match GNU-style \`stat -fc %T <path>\`.
if [[ "\$1" == "-fc" && "\$2" == "%T" ]]; then
    echo "$fs_type"
    exit 0
fi
exit 0
MOCK
    chmod +x "$STUB_BIN/stat"
}

# Extract `check_install_dir_filesystem` from 01-preflight.sh into a
# standalone snippet so sourcing it doesn't run the entire phase.
_extract_linux_fs_fn() {
    local out="$1"
    awk '
        /^check_install_dir_filesystem\(\) \{/ { capture=1 }
        capture { print }
        capture && /^\}/ { exit }
    ' "$BATS_TEST_DIRNAME/../../installers/phases/01-preflight.sh" > "$out"
}

# ---------------------------------------------------------------------------
# Linux: networked types should warn (non-fatal).
# ---------------------------------------------------------------------------

@test "linux preflight: nfs warns and does not exit fatally" {
    _make_gnu_stat_stub "nfs"
    local fn_file="$BATS_TEST_TMPDIR/fs-fn.sh"
    _extract_linux_fs_fn "$fn_file"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        log()   { :; }
        warn()  { echo "WARN: $1"; }
        error() { echo "ERROR: $1"; exit 1; }
        # Disable diskutil refinement (Linux has none anyway).
        source "'"$fn_file"'"
        check_install_dir_filesystem
        echo "EXIT_OK"
    '
    assert_success
    assert_output --partial "networked filesystem"
    assert_output --partial "EXIT_OK"
}

@test "linux preflight: cifs warns and does not exit fatally" {
    _make_gnu_stat_stub "cifs"
    local fn_file="$BATS_TEST_TMPDIR/fs-fn.sh"
    _extract_linux_fs_fn "$fn_file"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        log()   { :; }
        warn()  { echo "WARN: $1"; }
        error() { echo "ERROR: $1"; exit 1; }
        source "'"$fn_file"'"
        check_install_dir_filesystem
        echo "EXIT_OK"
    '
    assert_success
    assert_output --partial "networked filesystem"
    assert_output --partial "EXIT_OK"
}

# ---------------------------------------------------------------------------
# Linux: native POSIX filesystems must NOT warn or fatally exit.
# ---------------------------------------------------------------------------

@test "linux preflight: ext2/ext3/ext4 do not warn or exit fatally" {
    _make_gnu_stat_stub "ext2/ext3"
    local fn_file="$BATS_TEST_TMPDIR/fs-fn.sh"
    _extract_linux_fs_fn "$fn_file"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        log()   { :; }
        warn()  { echo "WARN: $1"; }
        error() { echo "ERROR: $1"; exit 1; }
        source "'"$fn_file"'"
        check_install_dir_filesystem
        echo "EXIT_OK"
    '
    assert_success
    refute_output --partial "WARN:"
    assert_output --partial "EXIT_OK"
}

# ---------------------------------------------------------------------------
# Linux: regression guard — exfat must still be fatal.
# ---------------------------------------------------------------------------

@test "linux preflight: exfat remains fatal (regression guard)" {
    _make_gnu_stat_stub "exfat"
    local fn_file="$BATS_TEST_TMPDIR/fs-fn.sh"
    _extract_linux_fs_fn "$fn_file"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        log()   { :; }
        warn()  { echo "WARN: $1"; }
        error() { echo "ERROR: $1"; exit 1; }
        source "'"$fn_file"'"
        check_install_dir_filesystem
        echo "EXIT_OK"
    '
    assert_failure
    assert_output --partial "ERROR:"
    refute_output --partial "EXIT_OK"
}

# ---------------------------------------------------------------------------
# macOS: networked types should set INSTALL_FS_NETWORKED=true (non-fatal).
# ---------------------------------------------------------------------------

@test "macos preflight: nfs sets INSTALL_FS_NETWORKED=true and is not fatal" {
    _make_bsd_stat_stub "nfs"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"'"
        test_install_dir_filesystem "'"$INSTALL_DIR"'"
        echo "TYPE=$INSTALL_FS_TYPE"
        echo "FATAL=$INSTALL_FS_FATAL"
        echo "NETWORKED=$INSTALL_FS_NETWORKED"
    '
    assert_success
    assert_output --partial "TYPE=nfs"
    assert_output --partial "FATAL=false"
    assert_output --partial "NETWORKED=true"
}

@test "macos preflight: smbfs sets INSTALL_FS_NETWORKED=true and is not fatal" {
    _make_bsd_stat_stub "smbfs"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"'"
        test_install_dir_filesystem "'"$INSTALL_DIR"'"
        echo "TYPE=$INSTALL_FS_TYPE"
        echo "FATAL=$INSTALL_FS_FATAL"
        echo "NETWORKED=$INSTALL_FS_NETWORKED"
    '
    assert_success
    assert_output --partial "TYPE=smbfs"
    assert_output --partial "FATAL=false"
    assert_output --partial "NETWORKED=true"
}

# ---------------------------------------------------------------------------
# macOS: native APFS must NOT flag networked or fatal.
# ---------------------------------------------------------------------------

@test "macos preflight: apfs is neither fatal nor networked" {
    _make_bsd_stat_stub "apfs"

    run bash -c '
        export PATH="'"$STUB_BIN:$PATH"'"
        source "'"$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"'"
        test_install_dir_filesystem "'"$INSTALL_DIR"'"
        echo "TYPE=$INSTALL_FS_TYPE"
        echo "FATAL=$INSTALL_FS_FATAL"
        echo "NETWORKED=$INSTALL_FS_NETWORKED"
    '
    assert_success
    assert_output --partial "FATAL=false"
    assert_output --partial "NETWORKED=false"
}
