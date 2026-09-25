#!/usr/bin/env bash
# End-to-end check of the doctor's GPU residency report. A fake `docker` on
# PATH serves a real llama-server load log, so the full doctor (report JSON,
# diagnosis, fix hint) and the `ods doctor` renderer run without a GPU.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"
FIXTURES="$ROOT_DIR/tests/fixtures/llama-placement"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

command -v python3 >/dev/null 2>&1 || { echo "[SKIP] python3 not available"; exit 0; }
[[ -f "$ROOT_DIR/.env" ]] && { echo "[SKIP] run from a source checkout, not an installed tree with .env"; exit 0; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/docker" <<'SH'
#!/usr/bin/env bash
# Minimal docker CLI double: one running ods-llama-server, nothing else.
case "$1" in
    info)
        case "${3:-}" in
            '{{.DockerRootDir}}') echo /var/lib/docker ;;
            '{{.SecurityOptions}}') echo '[name=seccomp,profile=builtin]' ;;
        esac
        exit 0 ;;
    compose) exit 0 ;;
    ps) echo ods-llama-server; exit 0 ;;
    inspect)
        [[ "${4:-}" == ods-llama-server ]] || exit 1
        case "$3" in
            '{{.State.Running}}') echo true ;;
            '{{.State.StartedAt}}') echo 2026-09-25T12:06:55.123456789Z ;;
            '{{.Config.Image}}') echo "${FAKE_LLAMA_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda-b9014}" ;;
            '{{json .Args}}') echo '["--model","/models/Qwen3.5-9B-Q4_K_M.gguf","--n-gpu-layers","auto","--ctx-size","65536"]' ;;
            '{{json .Config.Env}}') echo '["LLAMA_ARG_CACHE_TYPE_K=q8_0","DASHBOARD_API_KEY=not-a-real-secret-fixture","PATH=/usr/bin"]' ;;
            *) exit 1 ;;
        esac
        exit 0 ;;
    logs)
        [[ "$2" == --since && "$4" == ods-llama-server ]] || exit 1
        cat "$FAKE_LLAMA_LOG"
        exit 0 ;;
esac
exit 1
SH
chmod +x "$TMP_DIR/bin/docker"

run_doctor() {
    # $1 = load log fixture, $2 = GPU backend, $3 = report path
    env -u EXTERNAL_LLM_URL -u LEMONADE_EXTERNAL -u ODS_MODE -u LLM_BACKEND -u AMD_INFERENCE_RUNTIME \
        PATH="$TMP_DIR/bin:$PATH" FAKE_LLAMA_LOG="$1" FAKE_LLAMA_IMAGE="${FAKE_LLAMA_IMAGE:-}" \
        GPU_BACKEND="$2" NO_COLOR=1 \
        bash "$DOCTOR" "$3" > "$3.out" 2>&1 || true
    [[ -f "$3" ]] || { cat "$3.out" >&2; fail "doctor did not write $3"; }
}

field() {
    python3 -c 'import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
print(eval(sys.argv[2], {}, {"r": report}))' "$1" "$2"
}

# --- partial offload fails with a diagnosis and a fix ---------------------
report="$TMP_DIR/partial.json"
run_doctor "$FIXTURES/laptop-rtx5070-9b-64k-partial-b9014.txt" nvidia "$report"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["status"]')" == fail ]] \
    || fail "partial offload must report status fail"
[[ "$(field "$report" '(r["runtime"]["gpu_residency"]["layers_on_gpu"], r["runtime"]["gpu_residency"]["layers_total"])')" == "(29, 33)" ]] \
    || fail "report must carry the 29/33 layer split"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["source"]')" == docker:ods-llama-server ]] \
    || fail "report must name the inspected container"
[[ "$(field "$report" '[d["severity"] for d in r["diagnoses"] if d["id"] == "ODS-LLM-PARTIAL-GPU-OFFLOAD"]')" == "['blocker']" ]] \
    || fail "partial offload must add a blocker diagnosis"
[[ "$(field "$report" 'any("keeps 1024 MiB free as a safety margin" in h for h in r["autofix_hints"])')" == True ]] \
    || fail "fix hint must explain the fit margin"
# The laptop's GPU was idle: the fix is the margin and micro-batch, not
# closing other programs.
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["remedy"]')" == refit ]] \
    || fail "an idle GPU with room for the model must get the re-fit remedy"
[[ "$(field "$report" 'any("Closing other programs will not change this" in h and "LLAMA_ARG_FIT_TARGET=512" in h for h in r["autofix_hints"])')" == True ]] \
    || fail "fix hint must point at the fit margin and micro-batch settings"
grep -q "GPU residency: model partly on CPU: 29/33 layers on GPU" "$report.out" \
    || fail "doctor console output must show the partial placement"
if grep -q "not-a-real-secret-fixture" "$report" "$report.out"; then
    fail "container environment values other than LLAMA_ARG offload settings leaked"
fi
pass "partial offload reports FAIL with diagnosis and fix hint"

# `ods doctor` renders the report and exits non-zero on the failure.
renderer="$TMP_DIR/render.py"
awk '/^    python3 - "\$report_file" <<'"'"'PY'"'"'$/ {grab=1; next} grab && /^PY$/ {exit} grab {print}' \
    "$ROOT_DIR/ods-cli" > "$renderer"
[[ -s "$renderer" ]] || fail "could not extract the ods doctor renderer"
render_rc=0
python3 "$renderer" "$report" > "$TMP_DIR/render.out" 2>&1 || render_rc=$?
grep -q "GPU residency: model partly on CPU: 29/33 layers on GPU" "$TMP_DIR/render.out" \
    || fail "ods doctor must print the GPU residency failure"
grep -q "Fix: The GPU had room for the whole model: llama.cpp needed 6492 MiB" "$TMP_DIR/render.out" \
    || fail "ods doctor must print the residency fix"
[[ "$render_rc" == 1 ]] || fail "ods doctor must exit 1 on a partial offload (got $render_rc)"
pass "ods doctor prints the failure and exits 1"

# --- full offload passes without a diagnosis --------------------------------
report="$TMP_DIR/resident.json"
run_doctor "$FIXTURES/laptop-rtx5070-9b-64k-fit512-resident-b9014.txt" nvidia "$report"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["status"]')" == pass ]] \
    || fail "full offload must pass"
[[ "$(field "$report" '[d for d in r["diagnoses"] if d["id"] == "ODS-LLM-PARTIAL-GPU-OFFLOAD"]')" == "[]" ]] \
    || fail "full offload must not add the partial-offload diagnosis"
grep -q "GPU residency: 33/33 layers on GPU" "$report.out" || fail "console must show 33/33"
pass "full offload passes"

# --- a loaded model whose log does not state placement fails -------------
# llama.cpp b11146 at its default verbosity: the load succeeded but printed no
# placement line. A pin bump like this must not pass silently.
report="$TMP_DIR/unverified.json"
run_doctor "$FIXTURES/tower2-rtxpro6000-qwen35-2b-default-verbosity-b11146.txt" nvidia "$report"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["status"]')" == unverified ]] \
    || fail "a loaded model without placement lines must report unverified"
[[ "$(field "$report" '[d["severity"] for d in r["diagnoses"] if d["id"] == "ODS-LLM-GPU-PLACEMENT-UNVERIFIED"]')" == "['blocker']" ]] \
    || fail "unverified placement must add a blocker diagnosis"
grep -q "GPU residency: llama-server loaded the model but logged at verbosity 3" "$report.out" \
    || fail "doctor console output must show the unverified placement"
render_rc=0
python3 "$renderer" "$report" > "$TMP_DIR/render-unverified.out" 2>&1 || render_rc=$?
grep -q "Fix: Remove any LLAMA_ARG_LOG_VERBOSITY value below 4" "$TMP_DIR/render-unverified.out" \
    || fail "ods doctor must print the verbosity fix"
[[ "$render_rc" == 1 ]] || fail "ods doctor must exit 1 on unverified placement (got $render_rc)"
pass "unverified placement reports FAIL and ods doctor exits 1"

# --- Lemonade manages placement itself and is not judged --------------------
report="$TMP_DIR/lemonade.json"
run_doctor "$FIXTURES/tower2-rtxpro6000-qwen35-2b-default-verbosity-b11146.txt" amd "$report"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["status"]')" == skipped ]] \
    || fail "AMD (Lemonade) installs must skip the residency check"
report="$TMP_DIR/lemonade-image.json"
FAKE_LLAMA_IMAGE=ods-lemonade-server:latest \
    run_doctor "$FIXTURES/tower2-rtxpro6000-qwen35-2b-default-verbosity-b11146.txt" nvidia "$report"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["status"]')" == skipped ]] \
    || fail "a Lemonade llama-server container must skip the residency check"
pass "Lemonade skips the check"

# --- CPU-only installs are not judged ---------------------------------------
report="$TMP_DIR/cpu.json"
run_doctor "$FIXTURES/laptop-rtx5070-9b-64k-partial-b9014.txt" cpu "$report"
[[ "$(field "$report" 'r["runtime"]["gpu_residency"]["status"]')" == skipped ]] \
    || fail "CPU-only installs must skip the residency check"
pass "CPU-only install skips the check"
