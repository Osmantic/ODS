#!/usr/bin/env bash
# Regression guard for issue #1662: CPU budget values written to .env must
# always use dot decimals, even when the parent shell has a decimal-comma locale.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0
TMP_DIR=""

cleanup() {
    [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

require_scoped_awk() {
    local file="$1"
    local label="$2"
    if grep -Eq 'LC_ALL=C[[:space:]]+awk[[:space:]]+-v[[:space:]]+desired=' "$ROOT_DIR/$file"; then
        pass "$label scopes CPU formatting awk to C locale"
    else
        fail "$label must use LC_ALL=C for CPU formatting awk"
    fi
}

require_scoped_cpu_comparisons() {
    local file="$1"
    local label="$2"
    if grep -En 'awk "BEGIN \{ exit !\(' "$ROOT_DIR/$file" | grep -Fv 'LC_ALL=C awk' >/dev/null; then
        fail "$label has CPU comparison awk without LC_ALL=C"
    else
        pass "$label scopes CPU comparison awk to C locale"
    fi
}

echo ""
echo "Locale-safe CPU formatting"
echo "--------------------------"

require_scoped_awk "ods-cli" "ods-cli"
require_scoped_awk "installers/phases/06-directories.sh" "Linux phase 06"
require_scoped_awk "installers/macos/lib/env-generator.sh" "macOS env generator"
require_scoped_awk "installers/macos/ods-macos.sh" "macOS installer"
require_scoped_cpu_comparisons "ods-cli" "ods-cli"
require_scoped_cpu_comparisons "installers/phases/06-directories.sh" "Linux phase 06"
require_scoped_cpu_comparisons "installers/macos/lib/env-generator.sh" "macOS env generator"
require_scoped_cpu_comparisons "installers/macos/ods-macos.sh" "macOS installer"

TMP_DIR="$(mktemp -d)"
FAKE_BIN="$TMP_DIR/bin"
REAL_AWK="$(command -v awk)"
export ODS_TEST_REAL_AWK="$REAL_AWK"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/awk" <<'EOF'
#!/usr/bin/env bash
if [[ "${ODS_TEST_DECIMAL_COMMA:-}" == "1" && "${LC_ALL:-}" != "C" ]]; then
    if [[ "$*" == *'printf "%.1f"'* ]]; then
        printf '4,0\n'
        exit 0
    fi
    if [[ "$*" == *'BEGIN { exit !('* ]]; then
        printf 'awk: decimal comma parse failure\n' >&2
        exit 2
    fi
fi
exec "$ODS_TEST_REAL_AWK" "$@"
EOF
chmod +x "$FAKE_BIN/awk"

cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "info" ]]; then
    printf '4\n'
    exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
    exit 0
fi
if [[ "${1:-}" == "ps" ]]; then
    exit 0
fi
exit 0
EOF
chmod +x "$FAKE_BIN/docker"

result="$(
    PATH="$FAKE_BIN:$PATH" ODS_TEST_DECIMAL_COMMA=1 bash -c '
        source "$1"
        cap_cpu_value 8.0 4
    ' _ "$ROOT_DIR/installers/macos/lib/env-generator.sh"
)"

if [[ "$result" == "4.0" ]]; then
    pass "cap_cpu_value emits dot decimals under decimal-comma parent locale"
else
    fail "cap_cpu_value emitted '$result' instead of '4.0'"
fi

CLI_HOME="$TMP_DIR/ods-home"
mkdir -p "$CLI_HOME/lib" "$CLI_HOME/scripts"
cp "$ROOT_DIR/lib/service-registry.sh" "$CLI_HOME/lib/service-registry.sh"
cat > "$CLI_HOME/.env" <<'EOF'
GPU_BACKEND=nvidia
LLAMA_CPU_LIMIT=8.0
LLAMA_CPU_RESERVATION=2.0
TTS_CPU_LIMIT=8,0
TTS_CPU_RESERVATION=2,0
WHISPER_CPU_LIMIT=4,0
WHISPER_CPU_RESERVATION=1,0
HERMES_CPU_LIMIT=4,0
HERMES_CPU_RESERVATION=0,5
COMFYUI_CPU_LIMIT=8,0
COMFYUI_CPU_RESERVATION=2,0
EOF
cat > "$CLI_HOME/.compose-flags" <<'EOF'
-f docker-compose.base.yml
EOF
touch "$CLI_HOME/docker-compose.base.yml"

if PATH="$FAKE_BIN:$PATH" ODS_HOME="$CLI_HOME" ODS_TEST_DECIMAL_COMMA=1 bash "$ROOT_DIR/ods-cli" start >/dev/null 2>"$TMP_DIR/ods-cli-start.err"; then
    if grep -Eq '^(TTS|WHISPER|HERMES|COMFYUI)_CPU_(LIMIT|RESERVATION)=[0-9]+,[0-9]$' "$CLI_HOME/.env"; then
        fail "ods-cli start left comma decimal CPU values in .env"
    else
        expected_cpu_lines=(
            'TTS_CPU_LIMIT=4.0'
            'TTS_CPU_RESERVATION=2.0'
            'WHISPER_CPU_LIMIT=4.0'
            'WHISPER_CPU_RESERVATION=1.0'
            'HERMES_CPU_LIMIT=4.0'
            'HERMES_CPU_RESERVATION=0.5'
            'COMFYUI_CPU_LIMIT=4.0'
            'COMFYUI_CPU_RESERVATION=2.0'
        )
        missing_cpu_line=""
        for expected_cpu_line in "${expected_cpu_lines[@]}"; do
            if ! grep -Fxq "$expected_cpu_line" "$CLI_HOME/.env"; then
                missing_cpu_line="$expected_cpu_line"
                break
            fi
        done
        if [[ -z "$missing_cpu_line" ]]; then
            pass "ods-cli start rewrites corrupted comma CPU values with dot decimals"
        else
            fail "ods-cli start did not write expected line: $missing_cpu_line"
        fi
    fi
else
    fail "ods-cli start failed under decimal-comma locale: $(cat "$TMP_DIR/ods-cli-start.err")"
fi

echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All locale-safe CPU formatting tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    exit 0
else
    echo -e "${RED}Locale-safe CPU formatting tests failed${NC} ($PASSED passed, $FAILED failed)"
    exit 1
fi
