#!/usr/bin/env bats
# ============================================================================
# BATS tests for networked-filesystem detection in the preflight phase.
# ============================================================================
# Covers:
#   * macOS:  installers/macos/lib/preflight-fs.sh::test_install_dir_filesystem
#   * Linux:  installers/phases/01-preflight.sh::check_install_dir_filesystem
#
# Strategy: stub platform command output via PATH, then source the helper
# at the preflight boundary and assert on classification and fatal behavior.

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
    # bypassed. Linux tests do not invoke diskutil.
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

# BSD stat reports a file type marker, never the underlying filesystem.
# Model Apple's mount output, including a root mount and a nested volume.
_make_bsd_stat_stub() {
    export ODS_TEST_FS_TYPE="$1"
    cat > "$STUB_BIN/stat" <<'MOCK'
#!/bin/bash
printf '/\n'
MOCK
    cat > "$STUB_BIN/mount" <<'MOCK'
#!/bin/bash
[[ $# -eq 0 ]] || exit 99
printf '/dev/disk1 on / (apfs, local, read-only)\n'
printf '/dev/disk2 on %s (%s, local)\n' "$(cd "$INSTALL_DIR" && pwd -P)" "$ODS_TEST_FS_TYPE"
MOCK
    chmod +x "$STUB_BIN/stat" "$STUB_BIN/mount"
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

@test "macos preflight: non-POSIX volumes remain fatal without diskutil" {
    for fs_type in exfat msdos ntfs; do
        _make_bsd_stat_stub "$fs_type"
        run env PATH="$STUB_BIN:$PATH" bash -eu -o pipefail -c '
            source "$1"
            test_install_dir_filesystem "$INSTALL_DIR/new/ods"
            [[ "$INSTALL_FS_TYPE" == "$ODS_TEST_FS_TYPE" ]]
            [[ "$INSTALL_FS_FATAL" == true && "$INSTALL_FS_NETWORKED" == false ]]
        ' bash "$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"
        assert_success
    done
}

@test "macos preflight: longest mount wins for literal paths and symlinked parents" {
    export INSTALL_DIR="$BATS_TEST_TMPDIR/External on Disk (backup) [1]"
    mkdir -p "$INSTALL_DIR/projects"
    ln -s "$INSTALL_DIR/projects" "$BATS_TEST_TMPDIR/shortcut"
    _make_bsd_stat_stub "smbfs"
    run env PATH="$STUB_BIN:$PATH" bash -eu -o pipefail -c '
        source "$1"
        test_install_dir_filesystem "$2/new/ods"
        [[ "$INSTALL_FS_TYPE" == smbfs && "$INSTALL_FS_NETWORKED" == true ]]
    ' bash "$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh" "$BATS_TEST_TMPDIR/shortcut"
    assert_success
}

@test "macos preflight: sibling prefix is not the containing volume" {
    _make_bsd_stat_stub "exfat"
    mkdir -p "$INSTALL_DIR-other"
    run env PATH="$STUB_BIN:$PATH" bash -eu -o pipefail -c '
        source "$1"
        test_install_dir_filesystem "$INSTALL_DIR-other/new"
        [[ "$INSTALL_FS_TYPE" == apfs && "$INSTALL_FS_FATAL" == false ]]
    ' bash "$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"
    assert_success
}

@test "macos preflight: failed mount inspection retains the optional diskutil fallback" {
    _make_bsd_stat_stub "exfat"
    printf '#!/bin/bash\nexit 1\n' > "$STUB_BIN/mount"
    printf '#!/bin/bash\nprintf "File System Personality: ExFAT\\n"\n' > "$STUB_BIN/diskutil"
    run env PATH="$STUB_BIN:$PATH" bash -eu -o pipefail -c '
        source "$1"
        test_install_dir_filesystem "$INSTALL_DIR"
        [[ "$INSTALL_FS_TYPE" == exfat && "$INSTALL_FS_FATAL" == true ]]
    ' bash "$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"
    assert_success
}

@test "macos preflight: inspection failures stay unknown without aborting strict shells" {
    _make_bsd_stat_stub "exfat"
    printf '#!/bin/bash\nexit 1\n' > "$STUB_BIN/mount"
    run env PATH="$STUB_BIN:$PATH" bash -eu -o pipefail -c '
        source "$1"
        test_install_dir_filesystem "$INSTALL_DIR"
        [[ "$INSTALL_FS_TYPE" == unknown ]]
    ' bash "$BATS_TEST_DIRNAME/../../installers/macos/lib/preflight-fs.sh"
    assert_success
}
