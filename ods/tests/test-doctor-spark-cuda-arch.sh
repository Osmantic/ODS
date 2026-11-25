#!/bin/bash
# =============================================================================
# Test: ods doctor's compute capability 12.1 check (DGX Spark, RTX Spark)
# =============================================================================
# The doctor flags a llama-server without sm_121 kernels on a GPU of compute
# capability 12.1: the DGX Spark GB10 and the RTX Spark N1X. Runs the shipped
# shell probe with stubbed nvidia-smi and docker, then the shipped report
# writer, the doctor summary and the `ods doctor` display on its report.
#
# Run: bash tests/test-doctor-spark-cuda-arch.sh
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"
CLI="$ROOT_DIR/ods-cli"
PYTHON="${PYTHON_CMD:-python3}"
TMP_DIR="$(mktemp -d -t ods-doctor-spark-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

command -v "$PYTHON" >/dev/null 2>&1 || { echo "[FAIL] $PYTHON is required"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "[FAIL] jq is required"; exit 1; }

PASS=0
FAIL=0
assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        echo "        expected: $expected"
        echo "        actual:   $actual"
        FAIL=$((FAIL + 1))
    fi
}
assert_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        echo "        missing:  $needle"
        echo "        in:       $haystack"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== The shell probe ==="
# awk, not grep, so a missing anchor reaches the [FAIL] below under pipefail.
probe_start="$(awk 'index($0, "# Compute capability 12.1 CUDA arch check") == 1 { print NR; exit }' "$DOCTOR")"
probe_next="$(awk -v start="${probe_start:-0}" 'NR > start && /^# Collect extension diagnostics/ { print NR; exit }' "$DOCTOR")"
[[ -n "$probe_start" && -n "$probe_next" ]] || { echo "[FAIL] cannot locate the doctor's Spark probe"; exit 1; }
probe="$(sed -n "${probe_start},$((probe_next - 1))p" "$DOCTOR")"
eval "_doctor_spark_probe() {
$probe
}"

SPARK_ROW="$(sed -n '2p' "$ROOT_DIR/tests/fixtures/rtx-spark/nvidia-smi-wsl.txt")"
N1X="$(cut -d',' -f1 <<< "$SPARK_ROW")"
BAD_ARCHS='system_info: n_threads = 20 | CUDA : ARCHS = 500,610,700,750,800,860,890,1200 | USE_GRAPHS = 1 |'
GOOD_ARCHS='system_info: n_threads = 20 | CUDA : ARCHS = 1210 | BLACKWELL_NATIVE_FP4 = 1 |'

# Usage: run_probe <backend> <name, compute_cap> <containers> <log line> <daemon>
run_probe() (
    GPU_BACKEND="$1"
    SMI_ROW="$2"
    CONTAINERS="$3"
    LOG_LINE="$4"
    DOCKER_DAEMON="$5"
    nvidia-smi() { printf '%s\n' "$SMI_ROW"; }
    docker() {
        case "$1" in
            ps) printf '%s\n' "$CONTAINERS" ;;
            logs) printf '%s\n' "$LOG_LINE" ;;
            *) return 1 ;;
        esac
    }
    _doctor_spark_probe
    printf '%s|%s|%s|%s\n' "$DGX_SPARK_GPU" "$DGX_SPARK_COMPUTE_CAP" "$DGX_SPARK_CUDA_ARCH_STATUS" "$DGX_SPARK_CUDA_ARCH_MESSAGE"
)

assert_eq "GB10 on a CUDA 12 build warns, naming both Sparks" \
    "true|12.1|warn|DGX Spark / RTX Spark GPU (compute capability 12.1) detected, but llama-server reports CUDA archs '500,610,700,750,800,860,890,1200' without sm_121." \
    "$(run_probe nvidia 'NVIDIA GB10, 12.1' ods-llama-server "$BAD_ARCHS" true)"
assert_eq "RTX Spark N1X on a CUDA 12 build warns" \
    "true|12.1|warn|DGX Spark / RTX Spark GPU (compute capability 12.1) detected, but llama-server reports CUDA archs '500,610,700,750,800,860,890,1200' without sm_121." \
    "$(run_probe nvidia "$N1X, 12.1" ods-llama-server "$BAD_ARCHS" true)"
assert_eq "RTX Spark N1X on the CUDA 13 build passes" \
    "true|12.1|pass|llama-server includes sm_121 kernels for this DGX Spark / RTX Spark GPU." \
    "$(run_probe nvidia "$N1X, 12.1" ods-llama-server "$GOOD_ARCHS" true)"
assert_eq "no ARCHS line in the log is unknown" \
    "true|12.1|unknown|DGX Spark / RTX Spark GPU (compute capability 12.1) detected, but llama-server CUDA archs were not found in logs." \
    "$(run_probe nvidia "$N1X, 12.1" ods-llama-server 'llama-server started' true)"
assert_eq "no llama-server container is unknown" \
    "true|12.1|unknown|DGX Spark / RTX Spark GPU (compute capability 12.1) detected, but ods-llama-server is not available for CUDA arch inspection." \
    "$(run_probe nvidia "$N1X, 12.1" ods-open-webui '' true)"
assert_eq "an RTX 5090 (12.0) is not checked" "false|12.0|unknown|" \
    "$(run_probe nvidia 'NVIDIA GeForce RTX 5090, 12.0' ods-llama-server "$BAD_ARCHS" true)"
assert_eq "a non-NVIDIA backend is not checked" "false||unknown|" \
    "$(run_probe amd "$N1X, 12.1" ods-llama-server "$BAD_ARCHS" true)"

echo "=== The report writer, the doctor summary and ods doctor ==="
writer="$(awk '/^"\$PYTHON_CMD" - "\$CAP_FILE" .*<<.PY.$/ { on = 1; next } on && /^PY$/ { exit } on { print }' "$DOCTOR")"
summary="$(awk '/^"\$PYTHON_CMD" - "\$REPORT_FILE" <<.PY.$/ { on = 1; next } on && /^PY$/ { exit } on { print }' "$DOCTOR")"
display="$(awk '/python3 - "\$report_file" <<.PY.$/ { on = 1; next } on && /^PY$/ { exit } on { print }' "$CLI")"
[[ -n "$writer" && -n "$summary" && -n "$display" ]] || { echo "[FAIL] cannot locate the doctor's Python blocks"; exit 1; }
printf '%s\n' "$writer" > "$TMP_DIR/writer.py"
printf '%s\n' "$summary" > "$TMP_DIR/summary.py"
printf '%s\n' "$display" > "$TMP_DIR/display.py"
printf '%s\n' '{"recommended_tier": "T4"}' > "$TMP_DIR/cap.json"
printf '%s\n' '{"checks": [], "summary": {"blockers": 0, "warnings": 0}}' > "$TMP_DIR/pre.json"

# Usage: write_report <name> <status> <message>
write_report() (
    export LLM_STATUS=ok LLM_PROVIDER=llama-server LLM_URL=http://127.0.0.1:11434 LLM_MODEL=test
    export LLM_LOCAL_WARNING=false LLM_RECOVERY='' LLM_FAILURE='' ROOTLESS_DOCKER=false
    export ROOTLESS_SUBID_STATUS=skipped ROOTLESS_SUBID_USER='' ROOTLESS_SUBUID_TOTAL=0 ROOTLESS_SUBGID_TOTAL=0
    cd "$TMP_DIR"
    "$PYTHON" "$TMP_DIR/writer.py" "$TMP_DIR/cap.json" "$TMP_DIR/pre.json" "$TMP_DIR/$1.json" \
        true true true true true 3001 3000 '[]' disabled '' '' disabled '' \
        true 'NVIDIA GB10' 12.1 '500,610,700,750,800,860,890,1200' "$2" "$3" \
        0 8 0 0 "$TMP_DIR" true
)

warn_message="DGX Spark / RTX Spark GPU (compute capability 12.1) detected, but llama-server reports CUDA archs '500,610,700,750,800,860,890,1200' without sm_121."
write_report warn warn "$warn_message"
hint="$(jq -r '.autofix_hints[] | select(contains("sm_121"))' "$TMP_DIR/warn.json")"
assert_contains "the hint names the DGX Spark GB10" "DGX Spark GB10" "$hint"
assert_contains "the hint names the RTX Spark N1X" "RTX Spark N1X" "$hint"
assert_contains "the hint says what goes wrong" "'????'" "$hint"
assert_contains "the hint's recovery is an installer rerun" "Rerun the ODS installer" "$hint"
cuda13_tag="$(jq -r '.runtime.llama_server.container_images_by_compute["12.1"]' "$ROOT_DIR/config/backends/nvidia.json" | sed -n 's/.*:\(server-cuda13\)-b[0-9]*@.*/\1/p')"
assert_eq "the pinned 12.1 image is a server-cuda13 build" "server-cuda13" "$cuda13_tag"
assert_contains "the hint names that image" "($cuda13_tag)" "$hint"
assert_eq "the report keeps its key and message" "warn|$warn_message" \
    "$(jq -r '.runtime.dgx_spark_cuda_arch_check | "\(.status)|\(.message)"' "$TMP_DIR/warn.json")"

out="$("$PYTHON" "$TMP_DIR/summary.py" "$TMP_DIR/warn.json")"
assert_contains "the summary shows the warning" "  Spark GPU:     warning - $warn_message" "$out"
assert_contains "the summary links the ODS Discord" "Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC" "$out"
out="$("$PYTHON" "$TMP_DIR/display.py" "$TMP_DIR/warn.json" | sed 's/\x1b\[[0-9;]*m//g')"
assert_contains "ods doctor shows the warning" "DGX Spark / RTX Spark llama-server CUDA arch: $warn_message" "$out"

write_report pass pass "llama-server includes sm_121 kernels for this DGX Spark / RTX Spark GPU."
assert_eq "a pass gives no hint" "" "$(jq -r '.autofix_hints[] | select(contains("sm_121"))' "$TMP_DIR/pass.json")"
out="$("$PYTHON" "$TMP_DIR/summary.py" "$TMP_DIR/pass.json")"
assert_contains "the summary shows the pass" "  Spark GPU:     llama-server includes sm_121 kernels" "$out"
out="$("$PYTHON" "$TMP_DIR/display.py" "$TMP_DIR/pass.json" | sed 's/\x1b\[[0-9;]*m//g')"
assert_contains "ods doctor shows the pass" "DGX Spark / RTX Spark llama-server CUDA arch" "$out"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
