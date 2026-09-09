#!/bin/bash
# ============================================================================
# Extension library container hardening contract
# ============================================================================
# Every service in extensions/library that ships a Dockerfile must run as a
# non-root user, and any bind mount into that user's home must point at the
# path the image actually uses. A mismatch is silent: the container starts,
# then re-downloads its model cache outside the volume on every restart.
#
# Usage: ./tests/contracts/test-library-container-hardening.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIB_DIR="$ROOT_DIR/extensions/library/services"

PASSED=0
FAILED=0
pass() { echo "  [PASS] $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  [FAIL] $1"; FAILED=$((FAILED + 1)); }

echo "[contract] extension library container hardening"

[[ -d "$LIB_DIR" ]] || { echo "[FAIL] missing $LIB_DIR"; exit 1; }

shopt -s nullglob
dockerfiles=("$LIB_DIR"/*/Dockerfile)
shopt -u nullglob

[[ ${#dockerfiles[@]} -gt 0 ]] || { echo "[FAIL] no Dockerfiles found under $LIB_DIR"; exit 1; }

for dockerfile in "${dockerfiles[@]}"; do
    service="$(basename "$(dirname "$dockerfile")")"

    # 1. A USER directive must exist and must not be root.
    user_line="$(grep -E '^USER ' "$dockerfile" | tail -1 || true)"
    if [[ -z "$user_line" ]]; then
        fail "$service: no USER directive — container runs as root"
        continue
    fi

    user_name="$(awk '{print $2}' <<<"$user_line")"
    if [[ "$user_name" == "root" || "$user_name" == "0" ]]; then
        fail "$service: USER is $user_name"
        continue
    fi
    pass "$service: runs as non-root ($user_name)"

    # 2. That user must exist in the image.
    if grep -qE "useradd|adduser" "$dockerfile"; then
        pass "$service: creates its runtime user"
    else
        fail "$service: USER $user_name is never created"
    fi

    # 3. Any host mount into a home directory must match the image's cache path.
    compose="$(dirname "$dockerfile")/compose.yaml"
    [[ -f "$compose" ]] || continue

    xdg="$(grep -E '^ENV XDG_CACHE_HOME=' "$dockerfile" | tail -1 | cut -d= -f2- || true)"
    [[ -n "$xdg" ]] || continue

    # Mount targets that live under a home directory, per the compose file.
    while read -r target; do
        [[ -n "$target" ]] || continue
        if [[ "$target" == "$xdg"* ]]; then
            pass "$service: cache mount $target matches XDG_CACHE_HOME"
        else
            fail "$service: mount $target is under /home but XDG_CACHE_HOME is $xdg"
        fi
    done < <(grep -oE ':/home/[^ "]+' "$compose" | sed 's/^://' || true)

    # 4. Nothing should still be mounting into /root once we are non-root.
    if grep -qE ':/root/' "$compose"; then
        fail "$service: compose still mounts into /root while USER is $user_name"
    else
        pass "$service: no leftover /root mounts"
    fi
done

echo
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
