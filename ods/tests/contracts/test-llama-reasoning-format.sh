#!/usr/bin/env bash
# Contract: every native llama.cpp launcher pins --reasoning-format.
#
# LLAMA_REASONING is documented in .env.schema.json as "off (default) | auto |
# on. Off prevents thinking models from consuming the entire token budget on
# internal reasoning." llama.cpp has its own default, so a launcher that never
# passes --reasoning-format does not merely ignore the operator's setting -- it
# leaves reasoning at whatever the llama.cpp build prefers, which is the
# behaviour the default exists to prevent.
#
# Docker is exempt: docker-compose.base.yml passes LLAMA_ARG_REASONING into the
# container environment, which llama.cpp reads natively.
#
# The .env values (off/on/auto) are not llama.cpp's vocabulary, so each
# launcher also has to map them; the check below requires both the flag and the
# mapping input.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAILURES=0

fail() {
    echo "[FAIL] $*" >&2
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "[PASS] $*"
}

native_launchers=(
    bin/ods-host-agent.py
    installers/macos/install-macos.sh
    installers/macos/ods-macos.sh
    scripts/bootstrap-upgrade.sh
    installers/windows/install-windows.ps1
    installers/windows/ods.ps1
)

for target in "${native_launchers[@]}"; do
    path="$ROOT_DIR/$target"
    if [[ ! -f "$path" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
        continue
    fi
    if ! grep -q -- '--reasoning-format' "$path"; then
        fail "$target starts llama-server without --reasoning-format; LLAMA_REASONING is ignored and thinking mode falls back to the llama.cpp default"
        continue
    fi
    if ! grep -q 'LLAMA_REASONING' "$path"; then
        fail "$target passes --reasoning-format but never reads LLAMA_REASONING, so the documented setting has no effect"
        continue
    fi
    pass "$target pins --reasoning-format from LLAMA_REASONING"
done

# Docker's route is the container environment rather than an argv flag.
if grep -q 'LLAMA_ARG_REASONING=${LLAMA_REASONING' "$ROOT_DIR/docker-compose.base.yml"; then
    pass "docker-compose.base.yml forwards LLAMA_REASONING to the container environment"
else
    fail "docker-compose.base.yml no longer forwards LLAMA_REASONING as LLAMA_ARG_REASONING"
fi

if (( FAILURES > 0 )); then
    echo ""
    echo "[FAIL] llama.cpp reasoning-format contract"
    exit 1
fi

echo ""
echo "[PASS] every llama.cpp launch path applies LLAMA_REASONING"
