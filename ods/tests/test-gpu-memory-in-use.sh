#!/usr/bin/env bash
# The requirements check (phase 04) warns a fresh local NVIDIA install when
# other programs already hold much of a GPU's memory, before anything
# downloads:
#   - installers/lib/detection.sh ods_gpu_memory_in_use_report names each GPU
#     where more than max(4 GiB, 10% of the card) is in use, and nothing for a
#     desktop session or a unified-memory GPU that reports [N/A];
#   - phase 04's block prints the warning on a fresh install, even when
#     nvidia-smi fails after printing its rows, and stays quiet on a rerun
#     (its own model holds memory), for an external, gateway-only,
#     host-native or cloud model (nothing runs on these GPUs), on other
#     backends and when nvidia-smi prints nothing.
# Needs no GPU.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE04="$ROOT_DIR/installers/phases/04-requirements.sh"
PASS=0
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; PASS=$((PASS + 1)); }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# shellcheck source=../installers/lib/detection.sh
SCRIPT_DIR="$ROOT_DIR" source "$ROOT_DIR/installers/lib/detection.sh"

report() { printf '%b' "$1" | ods_gpu_memory_in_use_report; }

# tower2 (2026-10-27): another model server held about 82 GiB of each card.
[[ "$(report '0, 97887, 83973\n1, 97887, 83953\n')" == $'GPU 0: 82 of 96 GiB in use\nGPU 1: 82 of 96 GiB in use' ]] \
    || fail "busy GPUs must be named: $(report '0, 97887, 83973\n1, 97887, 83953\n')"
# A desktop session, and the 4 GiB floor on a 24 GB card.
[[ -z "$(report '0, 24564, 1200\n')" && -z "$(report '0, 24564, 4096\n')" \
    && "$(report '0, 24564, 4097\n')" == 'GPU 0: 4 of 24 GiB in use' ]] \
    || fail "a 24 GB card is busy only above 4 GiB in use"
# 10% of a 96 GB card is the floor there.
[[ -z "$(report '3, 97887, 9788\n')" && "$(report '3, 97887, 9789\n')" == 'GPU 3: 10 of 96 GiB in use' ]] \
    || fail "a 96 GB card is busy only above 10% in use"
[[ -z "$(report '0, [N/A], [N/A]\n')" && -z "$(report '0, 0, 0\n')" && -z "$(report '')" ]] \
    || fail "rows without memory numbers must be skipped"
pass "the report names GPUs with more than max(4 GiB, 10%) in use and skips desktops and [N/A] rows"

block="$(sed -n '/^# A fresh install sizes its model and services for each GPU.s whole memory\./,/^fi$/p' "$PHASE04")"
[[ -n "$block" ]] || fail "phase 04's GPU memory warning block was not found"

fakebin="$tmp/bin"
mkdir -p "$fakebin"
cat > "$fakebin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_CALLS"
printf '%b' "$FAKE_ROWS"
exit "${FAKE_STATUS:-0}"
EOF
chmod +x "$fakebin/nvidia-smi"

# run NAME [statements...]: phase 04's block with the given inputs.
run() {
    local name="$1" dir="$tmp/$1" rc=0
    shift
    mkdir -p "$dir/install"
    : > "$dir/calls"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'SCRIPT_DIR=%q\nINSTALL_DIR=%q\nLOG_FILE=%q\n' "$ROOT_DIR" "$dir/install" "$dir/install.log"
        printf 'export PATH=%q FAKE_CALLS=%q\n' "$fakebin:$PATH" "$dir/calls"
        printf '%s\n' 'source "$SCRIPT_DIR/installers/lib/detection.sh"'
        printf '%s\n' 'ai() { printf "AI %s\n" "$*"; }' 'ai_warn() { printf "WARN %s\n" "$*"; }' \
            'log() { printf "LOG %s\n" "$*" >> "$LOG_FILE"; }' \
            'ods_native_llm_requested() { [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]; }'
        printf '%s\n' 'GPU_BACKEND=nvidia TIER=NV_ULTRA EXTERNAL_LLM_URL="" ODS_GATEWAY_ONLY=false NATIVE_LLM_BASE_URL=""' \
            "export FAKE_ROWS='0, 97887, 83973\n1, 97887, 1200\n'"
        printf '%s\n' "$@" "$block" 'echo "DONE"'
    } > "$dir/run.bash"
    bash "$dir/run.bash" > "$dir/out" 2> "$dir/err" || rc=$?
    [[ "$rc" == 0 ]] || fail "$name: the block failed (rc=$rc): $(cat "$dir/err")"
}
warned() {
    [[ "$(cat "$tmp/$1/out")" == "WARN Other programs already use GPU memory:
AI   GPU 0: 82 of 96 GiB in use
AI   ODS sizes its model and services for each GPU's whole memory: stop those programs before ODS starts, or its model may not load.
DONE" ]]
}
quiet() { [[ "$(cat "$tmp/$1/out")" == DONE ]]; }

run fresh
warned fresh || fail "a fresh install must name the busy GPU and what to do: $(cat "$tmp/fresh/out")"
grep -qx -- '--query-gpu=index,memory.total,memory.used --format=csv,noheader,nounits' "$tmp/fresh/calls" \
    || fail "the warning must read index, total and used memory: $(cat "$tmp/fresh/calls")"
grep -qx 'LOG GPU memory in use before install: GPU 0: 82 of 96 GiB in use' "$tmp/fresh/install.log" \
    || fail "the warning must reach the install log"
run partial 'export FAKE_STATUS=15'
warned partial && grep -qx 'LOG nvidia-smi exited 15 while listing GPU memory' "$tmp/partial/install.log" \
    || fail "rows printed before an nvidia-smi failure must still be reported: $(cat "$tmp/partial/out")"
pass "a fresh local NVIDIA install names the busy GPUs before anything downloads, even when nvidia-smi then fails"

run idle "export FAKE_ROWS='0, 97887, 600\n'"
quiet idle || fail "idle GPUs must not warn: $(cat "$tmp/idle/out")"
run smi-empty "export FAKE_ROWS='' FAKE_STATUS=9"
quiet smi-empty || fail "an nvidia-smi that prints nothing leaves nothing to report: $(cat "$tmp/smi-empty/out")"
run rerun 'printf "LLM_BACKEND=vllm\n" > "$INSTALL_DIR/.env"'
quiet rerun && [[ ! -s "$tmp/rerun/calls" ]] \
    || fail "a rerun must not judge GPUs its own model holds: $(cat "$tmp/rerun/out" "$tmp/rerun/calls")"
for route in 'EXTERNAL_LLM_URL=http://127.0.0.1:11434' 'ODS_GATEWAY_ONLY=true' \
             'NATIVE_LLM_BASE_URL=http://localhost:8080' 'TIER=CLOUD' 'GPU_BACKEND=amd'; do
    name="route-${route%%=*}"
    run "$name" "$route"
    quiet "$name" && [[ ! -s "$tmp/$name/calls" ]] || fail "$route runs no model on these GPUs, so it must not warn"
done
pass "idle GPUs, an empty nvidia-smi, a rerun, external, gateway-only, host-native, cloud and AMD stay quiet"

printf '\nResult: %s passed, 0 failed\n' "$PASS"
