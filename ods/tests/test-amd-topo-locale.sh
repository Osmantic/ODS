#!/usr/bin/env bash
# Regression: detect_amd_topo() must emit dot-decimal VRAM even when the parent
# shell has a decimal-comma locale (de_DE, fr_FR, ...). Otherwise awk prints the
# per-GPU size as e.g. "24,0" and the `memory_gb: (.[2] | tonumber)` jq step
# fails, so the entire AMD topology JSON cannot be built.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

command -v jq >/dev/null 2>&1 || { echo "[SKIP] jq is required"; exit 0; }

echo ""
echo "Locale-safe AMD VRAM formatting"
echo "-------------------------------"

# 1. Static guard: the per-GPU VRAM awk must be scoped to the C locale.
if grep -Eq 'LC_ALL=C[[:space:]]+awk[[:space:]]+-v[[:space:]]+bytes=' "$ROOT_DIR/installers/lib/amd-topo.sh"; then
    pass "detect_amd_topo scopes the VRAM awk to the C locale"
else
    fail "detect_amd_topo must use LC_ALL=C for the VRAM awk"
fi

# 2. Behavioural: drive the real detect_amd_topo with a mocked sysfs and an awk
# that mimics a decimal-comma locale (a real awk that swaps the dot for a comma
# on exactly the VRAM computation), unless the caller already forced LC_ALL=C.
FAKE_BIN="$TMP_DIR/bin"
mkdir -p "$FAKE_BIN"
export ODS_TEST_REAL_AWK="$(command -v awk)"
cat > "$FAKE_BIN/awk" <<'EOF'
#!/usr/bin/env bash
if [[ "${ODS_TEST_DECIMAL_COMMA:-}" == "1" && "${LC_ALL:-}" != "C" && "$*" == *"bytes / 1073741824"* ]]; then
    out="$(LC_ALL=C "$ODS_TEST_REAL_AWK" "$@")"
    printf '%s' "${out//./,}"
    exit 0
fi
exec "$ODS_TEST_REAL_AWK" "$@"
EOF
chmod +x "$FAKE_BIN/awk"
# amd-smi / rocminfo / rocm-smi / modinfo are absent on the test host; the
# detector degrades to sysfs. Stub them as failing so nothing external answers.
for tool in amd-smi rocminfo rocm-smi modinfo; do
    printf '#!/bin/sh\nexit 1\n' > "$FAKE_BIN/$tool"
    chmod +x "$FAKE_BIN/$tool"
done

MOCK_DRM="$TMP_DIR/drm"
mkdir -p "$MOCK_DRM/card0/device"
printf '0x1002\n' > "$MOCK_DRM/card0/device/vendor"
printf '0x744c\n' > "$MOCK_DRM/card0/device/device"
printf '25769803776\n' > "$MOCK_DRM/card0/device/mem_info_vram_total"  # 24 GiB
printf '0\n' > "$MOCK_DRM/card0/device/mem_info_gtt_total"

runner="$TMP_DIR/run.sh"
cat > "$runner" <<'EOF'
set -uo pipefail
warn() { echo "WARN: $*" >&2; }
log() { :; }
ai() { :; }
# shellcheck source=/dev/null
source "$1/installers/lib/amd-topo.sh"
detect_amd_topo
EOF

topo_json="$(
    PATH="$FAKE_BIN:$PATH" ODS_TEST_DECIMAL_COMMA=1 ODS_DRM_SYS="$MOCK_DRM" \
        bash "$runner" "$ROOT_DIR" 2>/dev/null || true
)"

if printf '%s' "$topo_json" | jq -e '.gpus[0].memory_gb | type == "number"' >/dev/null 2>&1; then
    mem="$(printf '%s' "$topo_json" | jq -r '.gpus[0].memory_gb')"
    pass "detect_amd_topo builds valid topology JSON under a decimal-comma locale (memory_gb=$mem)"
else
    fail "detect_amd_topo produced no numeric memory_gb under a decimal-comma locale"
fi

echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All locale-safe AMD VRAM tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    exit 0
else
    echo -e "${RED}Locale-safe AMD VRAM tests failed${NC} ($PASSED passed, $FAILED failed)"
    exit 1
fi
