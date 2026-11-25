#!/usr/bin/env bash
# Contract: llama-server's compose CPU defaults must start on an 8-CPU host.
# Docker rejects `cpus` above the host CPU count ("range of CPUs is from 0.01
# to 8.00") instead of clamping, so a manual or recovery `docker compose up`
# without an installer-written LLAMA_CPU_LIMIT failed on common laptops (#8495).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_DEFAULT_CPUS=8

fail() { echo "[FAIL] $*" >&2; exit 1; }

default_of() {
    # Print the ${VAR:-default} value for VAR in a compose file, if present.
    sed -n "s/.*\${$1:-\([0-9.]*\)}.*/\1/p" "$2" | head -n 1
}

checked=0
for compose in "$ROOT_DIR"/docker-compose.*.yml; do
    limit="$(default_of LLAMA_CPU_LIMIT "$compose")"
    [[ -n "$limit" ]] || continue
    name="$(basename "$compose")"
    awk -v v="$limit" -v max="$MAX_DEFAULT_CPUS" 'BEGIN { exit !(v + 0 > 0 && v + 0 <= max + 0) }' \
        || fail "$name: LLAMA_CPU_LIMIT default $limit exceeds $MAX_DEFAULT_CPUS CPUs"
    reservation="$(default_of LLAMA_CPU_RESERVATION "$compose")"
    if [[ -n "$reservation" ]]; then
        awk -v r="$reservation" -v l="$limit" 'BEGIN { exit !(r + 0 <= l + 0) }' \
            || fail "$name: LLAMA_CPU_RESERVATION default $reservation exceeds limit $limit"
    fi
    checked=$((checked + 1))
done

[[ "$checked" -ge 4 ]] || fail "expected the GPU/CPU overlays to declare LLAMA_CPU_LIMIT defaults; found $checked"
echo "[PASS] llama-server CPU defaults fit an ${MAX_DEFAULT_CPUS}-CPU host ($checked overlays)"
