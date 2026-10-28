#!/usr/bin/env bash
# Phase 02 warns a fresh NVIDIA install when other programs already hold much
# of a GPU's memory, before anything downloads:
#   - installers/lib/detection.sh ods_gpu_memory_in_use_report names each GPU
#     where more than max(4 GiB, 10% of the card) is in use, and nothing for a
#     desktop session or a unified-memory GPU that reports [N/A];
#   - phase 02's block prints the warning on a fresh install, and stays quiet
#     on a rerun (its own model holds memory), on other backends and when
#     nvidia-smi fails.
# Needs no GPU.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE02="$ROOT_DIR/installers/phases/02-detection.sh"
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

block="$(sed -n '/^# A fresh install sizes its model for each GPU.s whole memory\./,/^fi$/p' "$PHASE02")"
[[ -n "$block" ]] || fail "phase 02's GPU memory warning block was not found"

fakebin="$tmp/bin"
mkdir -p "$fakebin"
cat > "$fakebin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_CALLS"
[[ "${FAKE_FAIL:-false}" != true ]] || { echo "NVIDIA-SMI has failed" >&2; exit 9; }
printf '%b' "$FAKE_ROWS"
EOF
chmod +x "$fakebin/nvidia-smi"

# run NAME [statements...]: phase 02's block with the given inputs.
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
            'log() { printf "LOG %s\n" "$*" >> "$LOG_FILE"; }'
        printf '%s\n' 'GPU_BACKEND=nvidia' "export FAKE_ROWS='0, 97887, 83973\n1, 97887, 1200\n'"
        printf '%s\n' "$@" "$block" 'echo "DONE"'
    } > "$dir/run.bash"
    bash "$dir/run.bash" > "$dir/out" 2> "$dir/err" || rc=$?
    [[ "$rc" == 0 ]] || fail "$name: the block failed (rc=$rc): $(cat "$dir/err")"
}

run fresh
[[ "$(cat "$tmp/fresh/out")" == "WARN Other programs already use GPU memory:
AI   GPU 0: 82 of 96 GiB in use
AI   ODS sizes its model for each GPU's whole memory: stop those programs before ODS starts its model, or it may not load.
DONE" ]] || fail "a fresh install must name the busy GPU and what to do: $(cat "$tmp/fresh/out")"
grep -qx -- '--query-gpu=index,memory.total,memory.used --format=csv,noheader,nounits' "$tmp/fresh/calls" \
    || fail "the warning must read index, total and used memory: $(cat "$tmp/fresh/calls")"
grep -qx 'LOG GPU memory in use before install: GPU 0: 82 of 96 GiB in use' "$tmp/fresh/install.log" \
    || fail "the warning must reach the install log"
pass "a fresh NVIDIA install names the busy GPUs before anything downloads"

run idle "export FAKE_ROWS='0, 97887, 600\n'"
[[ "$(cat "$tmp/idle/out")" == DONE ]] || fail "idle GPUs must not warn: $(cat "$tmp/idle/out")"
run rerun 'printf "LLM_BACKEND=vllm\n" > "$INSTALL_DIR/.env"'
[[ "$(cat "$tmp/rerun/out")" == DONE && ! -s "$tmp/rerun/calls" ]] \
    || fail "a rerun must not judge GPUs its own model holds: $(cat "$tmp/rerun/out" "$tmp/rerun/calls")"
run amd 'GPU_BACKEND=amd'
[[ "$(cat "$tmp/amd/out")" == DONE && ! -s "$tmp/amd/calls" ]] || fail "other backends must not query nvidia-smi"
run smi-fails 'export FAKE_FAIL=true'
[[ "$(cat "$tmp/smi-fails/out")" == DONE ]] || fail "an nvidia-smi failure must leave nothing to report: $(cat "$tmp/smi-fails/out")"
pass "idle GPUs, a rerun, another backend and a failing nvidia-smi stay quiet"

printf '\nResult: %s passed, 0 failed\n' "$PASS"
