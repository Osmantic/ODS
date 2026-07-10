#!/bin/bash
set -eo pipefail

# ANSI color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Determine directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Define absolute paths to scripts
ODS_PREFLIGHT="$ROOT_DIR/ods-preflight.sh"
ODS_DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"

fail() {
    echo -e "${RED}✗ $1${NC}" >&2
    exit 1
}

pass() {
    echo -e "${GREEN}✓ $1${NC}"
}

info() {
    echo -e "ℹ $1"
}

# ── Test: Subordinate UID/GID range verification in preflight and doctor ─────

info "Subuid/Subgid: Preflight warns when subordinate files are missing"
TMP_SUBID="$(mktemp -d)"
MOCK_BIN="$TMP_SUBID/bin"
mkdir -p "$MOCK_BIN"

cat > "$MOCK_BIN/docker" << 'EOF'
#!/bin/bash
if [[ "$*" == *"SecurityOptions"* ]]; then
    echo "rootless"
    exit 0
fi
exit 0
EOF
chmod +x "$MOCK_BIN/docker"

cat > "$MOCK_BIN/uname" << 'EOF'
#!/bin/bash
echo "Linux"
EOF
chmod +x "$MOCK_BIN/uname"

cat > "$MOCK_BIN/id" << 'EOF'
#!/bin/bash
echo "testuser"
EOF
chmod +x "$MOCK_BIN/id"

cat > "$MOCK_BIN/curl" << 'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$MOCK_BIN/curl"

# Set up environment variables
export PATH="$MOCK_BIN:$PATH"
export ODS_TEST_SUBUID_FILE="$TMP_SUBID/missing_subuid"
export ODS_TEST_SUBGID_FILE="$TMP_SUBID/missing_subgid"

# Create a minimal .env file so the preflight script loads it without errors
echo "GPU_BACKEND=cpu" > "$TMP_SUBID/.env"

# Run preflight in a subshell capturing stdout/stderr
PREFLIGHT_OUT="$TMP_SUBID/preflight.out"
(
    cd "$TMP_SUBID"
    # Copy preflight script to temp dir so it works relative to its location
    cp "$ODS_PREFLIGHT" ./ods-preflight.sh
    mkdir -p lib
    echo "load_env_file() { :; }" > lib/safe-env.sh
    bash ./ods-preflight.sh > "$PREFLIGHT_OUT" 2>&1 || true
)

grep -q "Subordinate UID allocation file.*missing" "$PREFLIGHT_OUT" \
    || { cat "$PREFLIGHT_OUT"; fail "Preflight did not warn on missing subuid file"; }
grep -q "Subordinate GID allocation file.*missing" "$PREFLIGHT_OUT" \
    || { cat "$PREFLIGHT_OUT"; fail "Preflight did not warn on missing subgid file"; }
pass "Preflight warns when subordinate files are missing"

info "Subuid/Subgid: Preflight warns when subordinate range is insufficient"
SUBUID_FILE="$TMP_SUBID/subuid_low"
SUBGID_FILE="$TMP_SUBID/subgid_low"
echo "testuser:100000:1000" > "$SUBUID_FILE"
echo "testuser:100000:1000" > "$SUBGID_FILE"

export ODS_TEST_SUBUID_FILE="$SUBUID_FILE"
export ODS_TEST_SUBGID_FILE="$SUBGID_FILE"

PREFLIGHT_OUT2="$TMP_SUBID/preflight2.out"
(
    cd "$TMP_SUBID"
    bash ./ods-preflight.sh > "$PREFLIGHT_OUT2" 2>&1 || true
)

grep -q "Subordinate UID range (1000).*smaller than.*65536" "$PREFLIGHT_OUT2" \
    || { cat "$PREFLIGHT_OUT2"; fail "Preflight did not warn on low subuid range"; }
grep -q "Subordinate GID range (1000).*smaller than.*65536" "$PREFLIGHT_OUT2" \
    || { cat "$PREFLIGHT_OUT2"; fail "Preflight did not warn on low subgid range"; }
pass "Preflight warns when subordinate range is insufficient"

info "Subuid/Subgid: Preflight passes when subordinate range is sufficient"
SUBUID_OK="$TMP_SUBID/subuid_ok"
SUBGID_OK="$TMP_SUBID/subgid_ok"
echo "testuser:100000:65536" > "$SUBUID_OK"
echo "testuser:100000:65536" > "$SUBGID_OK"

export ODS_TEST_SUBUID_FILE="$SUBUID_OK"
export ODS_TEST_SUBGID_FILE="$SUBGID_OK"

PREFLIGHT_OUT3="$TMP_SUBID/preflight3.out"
(
    cd "$TMP_SUBID"
    bash ./ods-preflight.sh > "$PREFLIGHT_OUT3" 2>&1 || true
)

grep -q "smaller than" "$PREFLIGHT_OUT3" && fail "Preflight warned on valid subuid/subgid range"
grep -q "missing" "$PREFLIGHT_OUT3" && fail "Preflight warned on missing valid subuid/subgid range"
pass "Preflight passes when subordinate range is sufficient"

info "Subuid/Subgid: Doctor warns when subordinate range is insufficient"
# Copy lib, scripts, and config from the repository to our temp folder first
cp -r "$ROOT_DIR/lib" "$TMP_SUBID/"
cp -r "$ROOT_DIR/scripts" "$TMP_SUBID/"
cp -r "$ROOT_DIR/config" "$TMP_SUBID/"

mkdir -p "$TMP_SUBID/data"
echo "{}" > "$TMP_SUBID/data/preflight-report.json"

cat > "$TMP_SUBID/scripts/preflight-engine.sh" << 'EOF'
#!/bin/bash
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --report)
            echo "{}" > "$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done
echo "GPU_BACKEND=cpu"
EOF
chmod +x "$TMP_SUBID/scripts/preflight-engine.sh"

cp "$ODS_DOCTOR" "$TMP_SUBID/scripts/ods-doctor.sh"

export ODS_TEST_SUBUID_FILE="$SUBUID_FILE"
export ODS_TEST_SUBGID_FILE="$SUBGID_FILE"

DOCTOR_OUT="$TMP_SUBID/doctor.out"
(
    cd "$TMP_SUBID"
    bash ./scripts/ods-doctor.sh > "$DOCTOR_OUT" 2>&1 || true
)

grep -q "rootless mode.*warning.*Subordinate UID range" "$DOCTOR_OUT" \
    || { cat "$DOCTOR_OUT"; fail "Doctor did not display subuid warning in stdout"; }
grep -q "Docker rootless subordinate ranges invalid" "$DOCTOR_OUT" \
    || { cat "$DOCTOR_OUT"; fail "Doctor did not output autofix hint for subuid range"; }
pass "Doctor warns when subordinate range is insufficient"

info "Subuid/Subgid: Preflight warns when subid files exist but have no entry for current user"
SUBUID_NO_ENTRY="$TMP_SUBID/subuid_no_entry"
SUBGID_NO_ENTRY="$TMP_SUBID/subgid_no_entry"
echo "otheruser:100000:65536" > "$SUBUID_NO_ENTRY"
echo "otheruser:100000:65536" > "$SUBGID_NO_ENTRY"

export ODS_TEST_SUBUID_FILE="$SUBUID_NO_ENTRY"
export ODS_TEST_SUBGID_FILE="$SUBGID_NO_ENTRY"

PREFLIGHT_OUT4="$TMP_SUBID/preflight4.out"
(
    cd "$TMP_SUBID"
    bash ./ods-preflight.sh > "$PREFLIGHT_OUT4" 2>&1 || true
)

grep -q "No subordinate UID range allocated for user" "$PREFLIGHT_OUT4" \
    || { cat "$PREFLIGHT_OUT4"; fail "Preflight did not warn when no subuid entry exists for user"; }
grep -q "No subordinate GID range allocated for user" "$PREFLIGHT_OUT4" \
    || { cat "$PREFLIGHT_OUT4"; fail "Preflight did not warn when no subgid entry exists for user"; }
pass "Preflight warns when subid files exist but have no entry for current user"

rm -rf "$TMP_SUBID"

echo ""
echo -e "${GREEN}All rootless-subuid-validation tests passed.${NC}"
