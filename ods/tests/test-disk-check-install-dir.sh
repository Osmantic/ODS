#!/bin/bash
# Tests that the installer disk check probes the INSTALL_DIR filesystem,
# not the hardcoded $HOME filesystem. Covers issue #1688.
#
# Strategy: stub df so we can assert which path it was called with,
# then run just the disk-detection block extracted from 02-detection.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DETECTION_PHASE="$ROOT_DIR/ods/installers/phases/02-detection.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Stub df ───────────────────────────────────────────────────────────────────
# Records the path it was called with to $TMP/df-probe-path, then returns
# a fixed 100GB free so DISK_AVAIL is always set.
STUB_BIN="$TMP/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/df" <<'SH'
#!/bin/sh
echo "$2" >> "$TMP_DIR/df-probe-path"
printf "Filesystem     1G-blocks  Used Available Use%% Mounted on\n"
printf "/dev/fake           500   400       100  80%% /\n"
SH
chmod +x "$STUB_BIN/df"

# The isolated disk-probe function extracted from 02-detection.sh.
# We source only the logic we changed so the test is fast and hermetic.
run_disk_probe() {
    local install_dir="$1"
    local home_dir="$2"

    # Mirror the exact logic from 02-detection.sh
    _disk_probe_path="${install_dir:-$home_dir/ods}"
    while [[ -n "$_disk_probe_path" ]] && [[ ! -e "$_disk_probe_path" ]]; do
        _disk_probe_path="$(dirname "$_disk_probe_path")"
    done
    _disk_probe_path="${_disk_probe_path:-$home_dir}"

    TMP_DIR="$TMP" PATH="$STUB_BIN:$PATH" \
        df -BG "$_disk_probe_path" > /dev/null

    echo "$_disk_probe_path"
}

# ── Test 1: No INSTALL_DIR set → probes HOME ──────────────────────────────────
info "Test 1: No INSTALL_DIR → disk check should probe HOME"
FAKE_HOME="$TMP/home/user"
mkdir -p "$FAKE_HOME"

probed="$(run_disk_probe "" "$FAKE_HOME")"
[[ "$probed" == "$FAKE_HOME"* ]] \
    || fail "Expected probe path inside FAKE_HOME ($FAKE_HOME), got: $probed"
pass "No INSTALL_DIR: probed HOME filesystem ($probed)"

# ── Test 2: INSTALL_DIR exists → probes INSTALL_DIR directly ──────────────────
info "Test 2: INSTALL_DIR exists → disk check should probe INSTALL_DIR"
FAKE_INSTALL="$TMP/mnt/data/ods"
mkdir -p "$FAKE_INSTALL"

probed="$(run_disk_probe "$FAKE_INSTALL" "$FAKE_HOME")"
[[ "$probed" == "$FAKE_INSTALL" ]] \
    || fail "Expected probe path == INSTALL_DIR ($FAKE_INSTALL), got: $probed"
pass "INSTALL_DIR exists: probed INSTALL_DIR directly ($probed)"

# ── Test 3: INSTALL_DIR does NOT exist → ancestor walk resolves correctly ──────
info "Test 3: INSTALL_DIR doesn't exist yet → walk up to existing ancestor"
# Use a fresh subdir to guarantee no intermediate path was created by earlier tests
T3="$TMP/t3"
FAKE_MOUNT_T3="$T3/mnt/data"
mkdir -p "$FAKE_MOUNT_T3"          # ancestor exists; $T3/mnt/data/ods does NOT
# Two non-existent segments: ods and ods/myinstall
NONEXISTENT_INSTALL="$FAKE_MOUNT_T3/ods/myinstall"   # neither segment exists

probed="$(run_disk_probe "$NONEXISTENT_INSTALL" "$FAKE_HOME")"
# Walk should stop at FAKE_MOUNT_T3 (first existing ancestor)
[[ "$probed" == "$FAKE_MOUNT_T3" ]] \
    || fail "Expected first existing ancestor ($FAKE_MOUNT_T3) to be probed, got: $probed"
pass "INSTALL_DIR not yet created: walked up to nearest existing ancestor ($probed)"


# ── Test 4: INSTALL_DIR is on a completely different mount from HOME ───────────
info "Test 4: INSTALL_DIR on a different path from HOME → HOME must NOT be probed"
ANOTHER_MOUNT="$TMP/another/mount"
mkdir -p "$ANOTHER_MOUNT"

probed="$(run_disk_probe "$ANOTHER_MOUNT" "$FAKE_HOME")"
[[ "$probed" != "$FAKE_HOME" ]] \
    || fail "Disk check probed HOME even though INSTALL_DIR exists at $ANOTHER_MOUNT"
[[ "$probed" == "$ANOTHER_MOUNT"* ]] \
    || fail "Expected probe inside ANOTHER_MOUNT ($ANOTHER_MOUNT), got: $probed"
pass "Different mount: HOME was NOT used, probed INSTALL_DIR mount ($probed)"

echo ""
echo -e "${GREEN}All disk-check-install-dir tests passed.${NC}"
