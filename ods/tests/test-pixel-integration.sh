#!/bin/bash
# ============================================================================
# ODS Tests — Pixel Integration Library
# ============================================================================
# Tests for ods/installers/lib/pixel-integration.sh
#
# Covers: host qualification (Ubuntu/Debian, WSL2+systemd, WSL no-systemd,
#         Ubuntu 22.04, Fedora, macOS-like), os-release safety (malicious
#         content, symlinks, duplicates), license exactness, true/false/auto
#         state machine, source/ref rejection, key shape/uniqueness.
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PATH="$SCRIPT_DIR/../installers/lib/pixel-integration.sh"

if [[ ! -f "$LIB_PATH" ]]; then
    echo "FATAL: pixel-integration.sh not found at $LIB_PATH"
    exit 1
fi

# Source the library
source "$LIB_PATH"

# ---- Test harness -----------------------------------------------------------

PASS=0
FAIL=0
TOTAL=0

pass() {
    PASS=$((PASS + 1))
    TOTAL=$((TOTAL + 1))
    echo "  ✓ PASS: $1"
}

fail() {
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
    echo "  ✗ FAIL: $1"
}

section() {
    echo ""
    echo "══ $1 ══"
}

# ---- Temporary fixtures -----------------------------------------------------

TMPDIR_TEST="$(mktemp -d)"
cleanup() {
    if [[ -n "${TMPDIR_TEST:-}" && "$TMPDIR_TEST" == /tmp/* && -d "$TMPDIR_TEST" ]]; then
        rm -rf -- "$TMPDIR_TEST"
    fi
}
trap cleanup EXIT

# Create os-release fixtures
create_os_release() {
    local path="$1"
    shift
    printf '%s\n' "$@" > "$path"
    chmod 0644 "$path"
}

# Create a mock /proc/1/comm
create_proc1_comm() {
    local path="$1"
    local content="$2"
    printf '%s\n' "$content" > "$path"
    chmod 0644 "$path"
}

# Create a mock /proc/version
create_proc_version() {
    local path="$1"
    local content="$2"
    printf '%s' "$content" > "$path"
    chmod 0644 "$path"
}

# ---- ods_pixel_host_qualified tests -----------------------------------------

section "ods_pixel_host_qualified — supported platforms"

# Ubuntu 24.04 + systemd
F_FIXTURE="$TMPDIR_TEST/ubuntu2404"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'NAME="Ubuntu"' \
    'VERSION="24.04 LTS (Noble Numbat)"' \
    'ID=ubuntu' \
    'ID_LIKE=debian' \
    'VERSION_ID="24.04"' \
    'PRETTY_NAME="Ubuntu 24.04 LTS"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 6.8.0-31-generic (Ubuntu 6.8.0-31.31-generic)..."
if ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "Ubuntu 24.04 + systemd is qualified"
else
    fail "Ubuntu 24.04 + systemd should be qualified"
fi

# Debian 12 + systemd
F_FIXTURE="$TMPDIR_TEST/debian12"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"' \
    'NAME="Debian GNU/Linux"' \
    'VERSION_ID="12"' \
    'VERSION="12 (bookworm)"' \
    'ID=debian'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 6.1.0 (Debian 6.1.0-12-amd64)..."
if ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "Debian 12 + systemd is qualified"
else
    fail "Debian 12 + systemd should be qualified"
fi

section "ods_pixel_host_qualified — WSL2 with systemd"

# WSL2 + Ubuntu 24.04 + systemd (should be qualified)
F_FIXTURE="$TMPDIR_TEST/wsl2-systemd"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'NAME="Ubuntu"' \
    'VERSION="24.04 LTS (Noble Numbat)"' \
    'ID=ubuntu' \
    'ID_LIKE=debian' \
    'VERSION_ID="24.04"' \
    'PRETTY_NAME="Ubuntu 24.04 LTS"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 5.15.133.1-microsoft-standard-WSL2..."
if ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "WSL2 + Ubuntu 24.04 + systemd is qualified"
else
    fail "WSL2 + Ubuntu 24.04 + systemd should be qualified"
fi

# WSL1-like kernel string remains unqualified even if a fixture claims systemd.
F_FIXTURE="$TMPDIR_TEST/wsl1-systemd"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" 'ID=ubuntu' 'VERSION_ID="24.04"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 4.4.0-Microsoft"
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "WSL1-like host is rejected"
else
    fail "WSL1-like host should not be qualified"
fi

section "ods_pixel_host_qualified — WSL without systemd"

# WSL + init (not systemd) — must be rejected
F_FIXTURE="$TMPDIR_TEST/wsl-no-systemd"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'NAME="Ubuntu"' \
    'VERSION="24.04 LTS"' \
    'ID=ubuntu' \
    'VERSION_ID="24.04"'
create_proc1_comm "$F_FIXTURE/proc1" "init"
create_proc_version "$F_FIXTURE/procver" "Linux version 5.15.133.1-microsoft-standard-WSL2..."
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "WSL without systemd is rejected"
else
    fail "WSL without systemd should be rejected"
fi

section "ods_pixel_host_qualified — unsupported platforms"

# Ubuntu 22.04
F_FIXTURE="$TMPDIR_TEST/ubuntu2204"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'ID=ubuntu' \
    'VERSION_ID="22.04"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 5.15.0..."
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "Ubuntu 22.04 is not qualified"
else
    fail "Ubuntu 22.04 should not be qualified"
fi

# Fedora-like
F_FIXTURE="$TMPDIR_TEST/fedora"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'ID=fedora' \
    'VERSION_ID="39"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 6.8.0..."
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver"; then
    pass "Fedora is not qualified"
else
    fail "Fedora should not be qualified"
fi

# macOS-like (uname != Linux) — simulate by checking non-Linux uname
# We cannot change uname, so we test with a non-existent PID1 path
F_FIXTURE="$TMPDIR_TEST/macos"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'ID=macos' \
    'VERSION_ID="14"'
# No /proc/1/comm on macOS — pass a nonexistent path
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/nonexistent" "$F_FIXTURE/procver"; then
    pass "macOS-like (no PID1 comm) is not qualified"
else
    fail "macOS-like should not be qualified"
fi

# ---- Malicious os-release (no code execution) --------------------------------

section "ods_pixel_host_qualified — malicious os-release content"

# os-release with command substitution in value
F_FIXTURE="$TMPDIR_TEST/malicious-cmdsubst"
mkdir -p "$F_FIXTURE"
MALICIOUS_MARKER="$TMPDIR_TEST/pwned-marker"
create_os_release "$F_FIXTURE/os-release" \
    'ID=ubuntu' \
    'VERSION_ID="24.04"' \
    "HACK=\$(touch $MALICIOUS_MARKER)"
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
# Should still work because we parse safely; the HACK line is just ignored
if ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1"; then
    pass "Malicious command-substitution os-release is safely parsed"
else
    fail "Malicious os-release should still be parseable (ID/VERSION_ID valid)"
fi

# Verify no side effects
if [[ -f "$MALICIOUS_MARKER" ]]; then
    fail "Malicious os-release caused file creation — code execution detected!"
else
    pass "No side effects from malicious os-release"
fi

# ---- os-release edge cases ---------------------------------------------------

section "ods_pixel_host_qualified — os-release edge cases"

# Symlink os-release — must be rejected
F_FIXTURE="$TMPDIR_TEST/symlink-os-release"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/real-release" 'ID=ubuntu' 'VERSION_ID="24.04"'
ln -s "$F_FIXTURE/real-release" "$F_FIXTURE/os-release-link"
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release-link" "$F_FIXTURE/proc1"; then
    pass "Symlink os-release is rejected"
else
    fail "Symlink os-release should be rejected"
fi

# Duplicate keys
F_FIXTURE="$TMPDIR_TEST/dup-keys"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'ID=ubuntu' \
    'VERSION_ID="24.04"' \
    'ID=debian'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1"; then
    pass "Duplicate ID key is rejected"
else
    fail "Duplicate ID key should be rejected"
fi

# Duplicate VERSION_ID
F_FIXTURE="$TMPDIR_TEST/dup-version"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" \
    'ID=ubuntu' \
    'VERSION_ID="24.04"' \
    'VERSION_ID="22.04"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
if ! ods_pixel_host_qualified "$F_FIXTURE/os-release" "$F_FIXTURE/proc1"; then
    pass "Duplicate VERSION_ID key is rejected"
else
    fail "Duplicate VERSION_ID key should be rejected"
fi

# ---- ods_pixel_license_accepted tests ----------------------------------------

section "ods_pixel_license_accepted"

# Exactly "true"
export PIXEL_LICENSE_ACCEPTED="true"
if ods_pixel_license_accepted; then
    pass 'PIXEL_LICENSE_ACCEPTED="true" is accepted'
else
    fail 'PIXEL_LICENSE_ACCEPTED="true" should be accepted'
fi

# "True" (capital T) — must be rejected
PIXEL_LICENSE_ACCEPTED="True"
if ! ods_pixel_license_accepted; then
    pass 'PIXEL_LICENSE_ACCEPTED="True" is rejected (case-sensitive)'
else
    fail 'PIXEL_LICENSE_ACCEPTED="True" should be rejected'
fi

# "yes"
PIXEL_LICENSE_ACCEPTED="yes"
if ! ods_pixel_license_accepted; then
    pass 'PIXEL_LICENSE_ACCEPTED="yes" is rejected'
else
    fail 'PIXEL_LICENSE_ACCEPTED="yes" should be rejected'
fi

# "1"
PIXEL_LICENSE_ACCEPTED="1"
if ! ods_pixel_license_accepted; then
    pass 'PIXEL_LICENSE_ACCEPTED="1" is rejected'
else
    fail 'PIXEL_LICENSE_ACCEPTED="1" should be rejected'
fi

# Empty
PIXEL_LICENSE_ACCEPTED=""
if ! ods_pixel_license_accepted; then
    pass 'PIXEL_LICENSE_ACCEPTED="" is rejected'
else
    fail 'PIXEL_LICENSE_ACCEPTED="" should be rejected'
fi

# Unset
unset PIXEL_LICENSE_ACCEPTED
if ! ods_pixel_license_accepted; then
    pass 'Unset PIXEL_LICENSE_ACCEPTED is rejected'
else
    fail 'Unset PIXEL_LICENSE_ACCEPTED should be rejected'
fi

# ---- ods_pixel_resolve_enablement tests --------------------------------------

section "ods_pixel_resolve_enablement"

# false -> hermes
result="$(ods_pixel_resolve_enablement false)"
if [[ "$result" == "hermes" ]]; then
    pass "enablement false -> hermes"
else
    fail "enablement false -> expected 'hermes', got '$result'"
fi

# true with no license -> error (nonzero)
PIXEL_LICENSE_ACCEPTED=""
unset PIXEL_LICENSE_ACCEPTED
QUALIFIED_FIXTURE="$TMPDIR_TEST/ubuntu2404"
if ! result="$(ods_pixel_resolve_enablement true "$QUALIFIED_FIXTURE/os-release" "$QUALIFIED_FIXTURE/proc1" "$QUALIFIED_FIXTURE/procver" 2>/dev/null)"; then
    pass "enablement true without license fails"
else
    fail "enablement true without license should fail"
fi

# auto with no license -> hermes
result="$(ods_pixel_resolve_enablement auto "$QUALIFIED_FIXTURE/os-release" "$QUALIFIED_FIXTURE/proc1" "$QUALIFIED_FIXTURE/procver")"
if [[ "$result" == "hermes" ]]; then
    pass "enablement auto without license -> hermes"
else
    fail "enablement auto without license -> expected 'hermes', got '$result'"
fi

# true with license but host not qualified (using fixture) -> error
PIXEL_LICENSE_ACCEPTED="true"
F_FIXTURE="$TMPDIR_TEST/fedora-en"
mkdir -p "$F_FIXTURE"
create_os_release "$F_FIXTURE/os-release" 'ID=fedora' 'VERSION_ID="39"'
create_proc1_comm "$F_FIXTURE/proc1" "systemd"
create_proc_version "$F_FIXTURE/procver" "Linux version 6.8.0"
if ! ods_pixel_resolve_enablement true "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver" 2>/dev/null; then
    pass "enablement true with license but unqualified host fails"
else
    fail "enablement true with license but unqualified host should fail"
fi

# auto with license but host not qualified -> hermes
result="$(ods_pixel_resolve_enablement auto "$F_FIXTURE/os-release" "$F_FIXTURE/proc1" "$F_FIXTURE/procver")"
if [[ "$result" == "hermes" ]]; then
    pass "enablement auto with license but unqualified host -> hermes"
else
    fail "enablement auto with license but unqualified host -> expected 'hermes', got '$result'"
fi

# Explicit true/auto succeed only when both predicates pass.
PIXEL_LICENSE_ACCEPTED="true"
result="$(ods_pixel_resolve_enablement true "$QUALIFIED_FIXTURE/os-release" "$QUALIFIED_FIXTURE/proc1" "$QUALIFIED_FIXTURE/procver")"
if [[ "$result" == "pixel" ]]; then
    pass "enablement true with qualified host and license -> pixel"
else
    fail "enablement true with qualified host and license should select pixel"
fi
result="$(ods_pixel_resolve_enablement auto "$QUALIFIED_FIXTURE/os-release" "$QUALIFIED_FIXTURE/proc1" "$QUALIFIED_FIXTURE/procver")"
if [[ "$result" == "pixel" ]]; then
    pass "enablement auto with qualified host and license -> pixel"
else
    fail "enablement auto with qualified host and license should select pixel"
fi

# Invalid value
if ! result="$(ods_pixel_resolve_enablement maybe 2>/dev/null)"; then
    pass "enablement 'maybe' fails"
else
    fail "enablement 'maybe' should fail"
fi

# Missing argument
if ! result="$(ods_pixel_resolve_enablement 2>/dev/null)"; then
    pass "enablement with no argument fails"
else
    fail "enablement with no argument should fail"
fi

# ---- ods_pixel_model_route_class tests --------------------------------------

section "ods_pixel_model_route_class"

for route_case in \
    "local||false|local" \
    "cloud||false|managed-gateway" \
    "hybrid||false|managed-gateway" \
    "local||true|managed-gateway" \
    "local|http://127.0.0.1:1234|false|managed-gateway"; do
    IFS='|' read -r route_mode route_url route_lemonade route_expected <<<"$route_case"
    result="$(ods_pixel_model_route_class "$route_mode" "$route_url" "$route_lemonade")"
    if [[ "$result" == "$route_expected" ]]; then
        pass "model route $route_mode/$route_lemonade/$route_url -> $route_expected"
    else
        fail "model route $route_mode/$route_lemonade/$route_url expected $route_expected, got $result"
    fi
done
unset route_case route_mode route_url route_lemonade route_expected

if ! ods_pixel_model_route_class invalid-mode "" false >/dev/null 2>&1; then
    pass "invalid Pixel model route mode is rejected"
else
    fail "invalid Pixel model route mode should be rejected"
fi

# ---- ods_pixel_validate_source tests -----------------------------------------

section "ods_pixel_validate_source"

# Valid GitHub URL + valid ref
PIXEL_SOURCE_URL="https://github.com/Osmantic/Pixel.git"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
if ods_pixel_validate_source; then
    pass "Valid GitHub URL + valid ref accepted"
else
    fail "Valid GitHub URL + valid ref should be accepted"
fi

# URL with credentials
PIXEL_SOURCE_URL="https://user:pass@github.com/Osmantic/Pixel.git"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "URL with credentials is rejected"
else
    fail "URL with credentials should be rejected"
fi

# Wrong repo URL
PIXEL_SOURCE_URL="https://github.com/Other/User.git"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Wrong repo URL is rejected"
else
    fail "Wrong repo URL should be rejected"
fi

# Short SHA ref
PIXEL_SOURCE_URL="https://github.com/Osmantic/Pixel.git"
PIXEL_SOURCE_REF="abcdef01"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Short SHA ref is rejected"
else
    fail "Short SHA ref should be rejected"
fi

# Uppercase hex ref
PIXEL_SOURCE_URL="https://github.com/Osmantic/Pixel.git"
PIXEL_SOURCE_REF="ABCDEF0123456789ABCDEF0123456789ABCDEF01"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Uppercase hex ref is rejected"
else
    fail "Uppercase hex ref should be rejected"
fi

# Branch name as ref
PIXEL_SOURCE_URL="https://github.com/Osmantic/Pixel.git"
PIXEL_SOURCE_REF="main"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Branch name ref is rejected"
else
    fail "Branch name ref should be rejected"
fi

# Missing URL
unset PIXEL_SOURCE_URL
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Missing URL is rejected"
else
    fail "Missing URL should be rejected"
fi

# Missing ref
PIXEL_SOURCE_URL="https://github.com/Osmantic/Pixel.git"
unset PIXEL_SOURCE_REF
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Missing ref is rejected"
else
    fail "Missing ref should be rejected"
fi

# Invalid scheme (file://)
PIXEL_SOURCE_URL="file:///some/path"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "file:// scheme is rejected"
else
    fail "file:// scheme should be rejected"
fi

# Valid local directory with .git
F_FIXTURE="$TMPDIR_TEST/local-git-dir"
mkdir -p "$F_FIXTURE/.git"
chmod 0755 "$F_FIXTURE" "$F_FIXTURE/.git"
PIXEL_SOURCE_URL="$F_FIXTURE"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
PIXEL_SOURCE_DIR="$(dirname "$F_FIXTURE")"
if ods_pixel_validate_source; then
    pass "Valid local directory with .git accepted"
else
    fail "Valid local directory with .git should be accepted"
fi

# Local directory that is a symlink
F_FIXTURE_REAL="$TMPDIR_TEST/local-git-real"
F_FIXTURE_LINK="$TMPDIR_TEST/local-git-link"
mkdir -p "$F_FIXTURE_REAL/.git"
ln -s "$F_FIXTURE_REAL" "$F_FIXTURE_LINK"
PIXEL_SOURCE_URL="$F_FIXTURE_LINK"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
unset PIXEL_SOURCE_DIR
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Symlink local directory is rejected"
else
    fail "Symlink local directory should be rejected"
fi

# Local directory without .git
F_FIXTURE="$TMPDIR_TEST/no-git-dir"
mkdir -p "$F_FIXTURE"
PIXEL_SOURCE_URL="$F_FIXTURE"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
unset PIXEL_SOURCE_DIR
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Local directory without .git is rejected"
else
    fail "Local directory without .git should be rejected"
fi

# Nonexistent local path
PIXEL_SOURCE_URL="$TMPDIR_TEST/does-not-exist"
PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
unset PIXEL_SOURCE_DIR
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Nonexistent local path is rejected"
else
    fail "Nonexistent local path should be rejected"
fi

# Path traversal via PIXEL_SOURCE_DIR
F_FIXTURE_REAL="$TMPDIR_TEST/traversal-target"
mkdir -p "$F_FIXTURE_REAL/.git"
chmod 0755 "$F_FIXTURE_REAL" "$F_FIXTURE_REAL/.git"
export PIXEL_SOURCE_URL="$F_FIXTURE_REAL"
export PIXEL_SOURCE_REF="abcdef0123456789abcdef0123456789abcdef01"
export PIXEL_SOURCE_DIR="$TMPDIR_TEST/unrelated-dir"
mkdir -p "$TMPDIR_TEST/unrelated-dir"
chmod 0755 "$TMPDIR_TEST/unrelated-dir"
if ! ods_pixel_validate_source 2>/dev/null; then
    pass "Path traversal outside PIXEL_SOURCE_DIR is rejected"
else
    fail "Path traversal outside PIXEL_SOURCE_DIR should be rejected"
fi

# ---- ods_pixel_activate_source_contract tests -------------------------------

section "ods_pixel_activate_source_contract"

if (
    unset PIXEL_SOURCE_URL PIXEL_SOURCE_REF PIXEL_SOURCE_DIR
    ods_pixel_activate_source_contract \
        "https://github.com/Osmantic/Pixel.git" \
        "abcdef0123456789abcdef0123456789abcdef01" ""
    python3 -c 'import os
assert os.environ["PIXEL_SOURCE_URL"] == "https://github.com/Osmantic/Pixel.git"
assert os.environ["PIXEL_SOURCE_REF"] == "abcdef0123456789abcdef0123456789abcdef01"
assert os.environ["PIXEL_SOURCE_DIR"] == ""'
); then
    pass "Validated Pixel source contract persists across installer phases"
else
    fail "Validated Pixel source contract was not exported for Phase 11"
fi

if ! (
    ods_pixel_activate_source_contract \
        "https://github.com/Osmantic/Pixel.git" "main" ""
); then
    pass "Invalid Pixel source contract is not activated"
else
    fail "Invalid Pixel source contract should fail before Phase 11"
fi

# ---- ods_pixel_generate_key tests --------------------------------------------

section "ods_pixel_generate_key"

# Key shape: exactly 64 lowercase hex
key1="$(ods_pixel_generate_key)"
if [[ "$key1" =~ ^[0-9a-f]{64}$ ]]; then
    pass "Key matches exactly 64 lowercase hex chars"
else
    fail "Key '$key1' does not match 64 lowercase hex"
fi

# Key length check
if [[ ${#key1} -eq 64 ]]; then
    pass "Key length is exactly 64"
else
    fail "Key length is ${#key1}, expected 64"
fi

# No extra output (only one line, no trailing whitespace beyond newline)
line_count="$(echo "$key1" | wc -l)"
if [[ "$line_count" -eq 1 ]]; then
    pass "Key output is exactly one line"
else
    fail "Key output is $line_count lines, expected 1"
fi

# Uniqueness: generate 20 keys and ensure they are all different
declare -A key_set
unique=0
duplicates=0
for _ in $(seq 1 20); do
    k="$(ods_pixel_generate_key)"
    if [[ -n "${key_set[$k]+x}" ]]; then
        duplicates=$((duplicates + 1))
    else
        key_set[$k]=1
        unique=$((unique + 1))
    fi
done
if [[ $unique -eq 20 && $duplicates -eq 0 ]]; then
    pass "20 generated keys are all unique"
else
    fail "Key uniqueness: $unique unique, $duplicates duplicates (expected 20/0)"
fi

section "ods_pixel_reconcile_installed_compose"
RECONCILE_SOURCE="$TMPDIR_TEST/reconcile-source"
RECONCILE_INSTALL="$TMPDIR_TEST/reconcile-install"
mkdir -p "$RECONCILE_SOURCE/extensions/services/pixel-edge" \
    "$RECONCILE_INSTALL/extensions/services/pixel-edge"

# Disabled source copied over an existing install must remove only the stale
# active fragment and retain the disabled source record.
printf '%s\n' 'services: {}' > "$RECONCILE_SOURCE/extensions/services/pixel-edge/compose.yaml.disabled"
printf '%s\n' 'services: { pixel-edge: {} }' > "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml"
cp "$RECONCILE_SOURCE/extensions/services/pixel-edge/compose.yaml.disabled" \
    "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml.disabled"
if ods_pixel_reconcile_installed_compose "$RECONCILE_SOURCE" "$RECONCILE_INSTALL" false \
    && [[ ! -e "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml" ]] \
    && [[ -f "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml.disabled" ]]; then
    pass "Pixel rollback removes the stale installed active Compose fragment"
else
    fail "Pixel rollback should remove only the stale installed active Compose fragment"
fi

# Re-enablement must retain the copied active fragment and remove a stale
# disabled twin so the installed tree has one unambiguous state.
rm -f -- "$RECONCILE_SOURCE/extensions/services/pixel-edge/compose.yaml.disabled"
printf '%s\n' 'services: { pixel-edge: {} }' > "$RECONCILE_SOURCE/extensions/services/pixel-edge/compose.yaml"
cp "$RECONCILE_SOURCE/extensions/services/pixel-edge/compose.yaml" \
    "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml"
printf '%s\n' 'stale' > "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml.disabled"
if ods_pixel_reconcile_installed_compose "$RECONCILE_SOURCE" "$RECONCILE_INSTALL" true \
    && [[ -f "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml" ]] \
    && [[ ! -e "$RECONCILE_INSTALL/extensions/services/pixel-edge/compose.yaml.disabled" ]]; then
    pass "Pixel re-enable keeps one active installed Compose fragment"
else
    fail "Pixel re-enable should keep one active installed Compose fragment"
fi

section "Pixel .env schema contract"
if python3 - "$SCRIPT_DIR/../.env.schema.json" <<'PY'
import json, sys

properties = json.load(open(sys.argv[1], encoding="utf-8"))["properties"]
expected = {
    "ENABLE_PIXEL", "PIXEL_AGENT_MODE", "PIXEL_LICENSE_ACCEPTED",
    "PIXEL_SOURCE_URL", "PIXEL_SOURCE_REF", "PIXEL_SOURCE_DIR",
    "PIXEL_OPENWEBUI_KEY", "PIXEL_INGRESS_RUNTIME_DIR",
    "PIXEL_PREVIEW_RUNTIME_DIR", "PIXEL_INGRESS_GID",
}
assert expected <= properties.keys()
assert properties["PIXEL_SOURCE_REF"]["pattern"] == "^[0-9a-f]{40}$"
assert properties["PIXEL_OPENWEBUI_KEY"]["minLength"] == 64
assert properties["PIXEL_OPENWEBUI_KEY"]["maxLength"] == 64
assert properties["PIXEL_PREVIEW_RUNTIME_DIR"]["enum"] == ["/run/ods-pixel-preview"]
assert properties["PIXEL_INGRESS_GID"]["minimum"] == 1
assert properties["PIXEL_LICENSE_ACCEPTED"]["type"] == "boolean"
PY
then
    pass "Pixel generated environment keys are defined by the strict schema"
else
    fail "Pixel generated environment keys must be defined by the strict schema"
fi

# ---- Summary -----------------------------------------------------------------

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Results: $PASS passed, $FAIL failed, $TOTAL total"
echo "═══════════════════════════════════════════════════════════════"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
