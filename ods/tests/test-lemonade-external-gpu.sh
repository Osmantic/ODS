#!/usr/bin/env bash
# The Windows Portal AMD route passes --lemonade-url/--lemonade-model and the
# Windows GPU to install-core. The flags must reach the phases, and the
# hardware scan must show that GPU instead of the CPU-only Linux probe.
# Variables below are read by the installer code this test evals.
# shellcheck disable=SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0
fail=0
check() {
    if eval "$1"; then echo "PASS: $2"; pass=$((pass + 1)); else echo "FAIL: $2"; fail=$((fail + 1)); fi
}

# Flag parsing: run only install-core's argument loop and exports.
parse_source="$(sed -n '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$ROOT/install-core.sh")"
[[ -n "$parse_source" ]] || { echo "FAIL: argument loop not found" >&2; exit 1; }
vram_source="$(sed -n '/^# Validate external Lemonade VRAM/,/^unset _lemonade_vram$/p' "$ROOT/install-core.sh")"
[[ -n "$vram_source" ]] || { echo "FAIL: VRAM validation block not found" >&2; exit 1; }
exports="$(sed -n '/^if \[\[ "\${LEMONADE_EXTERNAL,,}" == "true" \]\]; then$/,/^fi$/p' "$ROOT/install-core.sh")"
[[ -n "$exports" ]] || { echo "FAIL: Lemonade export block not found" >&2; exit 1; }
parsed="$(
    set -- --pixel --lemonade-url http://localhost:8080 --lemonade-model extra.Qwen3.5-9B-Q4_K_M.gguf \
        --lemonade-gpu-name 'AMD Radeon RX 9070 XT' --lemonade-gpu-vram-mb 16304 --tier 2
    LEMONADE_EXTERNAL=false LEMONADE_MODEL='' LEMONADE_GPU_NAME='' LEMONADE_GPU_VRAM_MB=''
    eval "$parse_source"
    eval "$vram_source"
    eval "$exports"
    bash -c 'printf "%s|%s|%s|%s|%s" "$LEMONADE_EXTERNAL" "$LEMONADE_BASE_URL" "$LEMONADE_MODEL" "$LEMONADE_GPU_NAME" "$LEMONADE_GPU_VRAM_MB"'
    printf '|%s|%s' "$ODS_MODE" "$TIER"
)"
check '[[ "$parsed" == "true|http://localhost:8080|extra.Qwen3.5-9B-Q4_K_M.gguf|AMD Radeon RX 9070 XT|16304|lemonade|2" ]]' \
    "Lemonade route flags are parsed and exported ($parsed)"
bad_vram_rc=0
( set -- --lemonade-gpu-vram-mb 16GB; eval "$parse_source"; eval "$vram_source" ) >/dev/null 2>&1 || bad_vram_rc=$?
check '[[ "$bad_vram_rc" -ne 0 ]]' "non-numeric --lemonade-gpu-vram-mb is rejected"

normalize_vram() (
    LEMONADE_GPU_VRAM_MB="$1"
    eval "$vram_source"
    printf '%s' "$LEMONADE_GPU_VRAM_MB"
)
for bad in '16GB' '-1' '1+1' '1.5' 'a[0]' '9223372036854775296' '999999999999999999999999999999'; do
    bad_vram_rc=0
    normalize_vram "$bad" >/dev/null 2>&1 || bad_vram_rc=$?
    check '[[ "$bad_vram_rc" -ne 0 ]]' "environment VRAM rejects $bad before arithmetic"
done
normalized="$(normalize_vram '')"
check '[[ -z "$normalized" ]]' 'empty environment VRAM retains automatic detection'
normalized="$(normalize_vram 00000)"
check '[[ "$normalized" == 0 ]]' 'zero-filled VRAM normalizes to decimal zero'
normalized="$(normalize_vram 016304)"
check '[[ "$normalized" == 16304 ]]' 'environment VRAM uses decimal rather than octal'
normalized="$(set -- --lemonade-gpu-vram-mb 08; LEMONADE_GPU_VRAM_MB=''; eval "$parse_source"; eval "$vram_source"; printf '%s' "$LEMONADE_GPU_VRAM_MB")"
check '[[ "$normalized" == 8 && $(( (normalized + 512) / 1024 )) -eq 0 ]]' 'CLI VRAM with an octal-invalid leading zero safely normalizes'
normalized="$(normalize_vram 9223372036854775295)"
check '[[ $(( (normalized + 512) / 1024 )) -eq 9007199254740991 ]]' 'largest accepted VRAM remains inside display arithmetic range'

# Hardware scan: the external GPU replaces the Linux probe's "None".
card_source="$(sed -n '/# An external Lemonade (Windows under WSL) runs the model on a GPU this/,/^    fi$/p' \
    "$ROOT/installers/phases/02-detection.sh")"
[[ -n "$card_source" ]] || { echo "FAIL: hardware card block not found" >&2; exit 1; }
card() {
    show_hardware_summary() { printf '%s|%s' "$1" "$2"; }
    GPU_NAME=None GPU_VRAM=0 CPU_INFO=cpu RAM_GB=47 DISK_AVAIL=896
    eval "$card_source"
}
shown="$(LEMONADE_EXTERNAL=true LEMONADE_GPU_NAME='AMD Radeon RX 9070 XT' LEMONADE_GPU_VRAM_MB=16304 card)"
check '[[ "$shown" == "AMD Radeon RX 9070 XT (Lemonade)|16" ]]' "hardware scan shows the Windows Lemonade GPU ($shown)"
shown="$(LEMONADE_EXTERNAL=false LEMONADE_GPU_NAME='AMD Radeon RX 9070 XT' LEMONADE_GPU_VRAM_MB=16304 card)"
check '[[ "$shown" == "None|0" ]]' "without an external Lemonade the Linux probe is shown ($shown)"

# The Linux GPU probe must not warn about CPU-only inference for this route.
fallback_source="$(sed -n '/# No GPU detected - fall back to CPU-only mode/,/^    return 1$/p' "$ROOT/installers/lib/detection.sh")"
[[ -n "$fallback_source" ]] || { echo "FAIL: CPU fallback block not found" >&2; exit 1; }
probe() {
    ai() { printf 'AI:%s\n' "$1"; }
    warn() { printf 'WARN:%s\n' "$1"; }
    log() { printf 'LOG:%s\n' "$1"; }
    eval "fallback() { ${fallback_source}
}"
    fallback || true
    printf 'BACKEND:%s|VRAM:%s|COUNT:%s' "$GPU_BACKEND" "$GPU_VRAM" "$GPU_COUNT"
}
said="$(LEMONADE_EXTERNAL=true LEMONADE_GPU_NAME='AMD Radeon RX 9070 XT' probe)"
check '[[ "$said" == "AI:"*"runs on AMD Radeon RX 9070 XT through Lemonade"* ]]' "Lemonade route replaces the CPU-only warning ($said)"
check '[[ "$said" == *"LOG:Model inference uses the external Lemonade GPU: AMD Radeon RX 9070 XT."* && "$said" != *"CPU-only mode"* && "$said" != *"CPU inference"* && "$said" != *"Consider adding a GPU"* ]]' \
    "external Lemonade log does not claim local CPU inference"
check '[[ "$said" == *"BACKEND:cpu|VRAM:0|COUNT:0" ]]' "external inference leaves the Linux GPU backend unchanged"
said="$(LEMONADE_EXTERNAL=false LEMONADE_GPU_NAME='' probe)"
check '[[ "$said" == "WARN:No GPU detected."* ]]' "other hosts keep the CPU-only warning"
check '[[ "$said" == *"LOG:CPU-only mode: llama.cpp will use CPU inference."* ]]' "local CPU inference keeps its diagnostic log"
said="$(LEMONADE_EXTERNAL=true LEMONADE_GPU_NAME='' probe)"
check '[[ "$said" == "WARN:No GPU detected."* && "$said" == *"LOG:CPU-only mode: llama.cpp will use CPU inference."* ]]' \
    "an external flag without a GPU name keeps the CPU diagnostics"

echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
