#!/usr/bin/env bash
# Contract: the macOS native launchers honour every optional llama.cpp tunable.
#
# On Docker the LLAMA_ARG_* variables reach llama.cpp through the container
# environment (docker-compose.base.yml passes them straight through, and
# llama.cpp reads LLAMA_ARG_* natively). The native launchers get no such
# passthrough -- ods-macos.sh exports .env keys under an ENV_ prefix, and the
# other two read the file with grep -- so each flag has to be appended to the
# llama_args array explicitly, or the setting is inert on Apple Silicon only.
#
# scripts/bootstrap-upgrade.sh is checked on the same `llama_args+=` append
# form as the other two: it also embeds a Windows PowerShell block that already
# handles these flags, so a bare grep for the flag name passes on that alone.

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

# "<llama.cpp flag> <documented .env variable>"
tunables=(
    "--flash-attn LLAMA_ARG_FLASH_ATTN"
    "--cache-type-k LLAMA_ARG_CACHE_TYPE_K"
    "--cache-type-v LLAMA_ARG_CACHE_TYPE_V"
    "--n-cpu-moe LLAMA_ARG_N_CPU_MOE"
    "--spec-type LLAMA_ARG_SPEC_TYPE"
    "--spec-draft-n-max LLAMA_ARG_SPEC_DRAFT_N_MAX"
    "--checkpoint-every-n-tokens LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"
    "--no-cache-prompt LLAMA_ARG_NO_CACHE_PROMPT"
)

macos_launchers=(
    installers/macos/install-macos.sh
    installers/macos/ods-macos.sh
    scripts/bootstrap-upgrade.sh
)

for target in "${macos_launchers[@]}"; do
    path="$ROOT_DIR/$target"
    if [[ ! -f "$path" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
        continue
    fi
    missing=()
    for entry in "${tunables[@]}"; do
        flag="${entry%% *}"
        variable="${entry##* }"
        if ! grep -qE "_?llama_args\+=\(${flag}\b" "$path"; then
            missing+=("$flag ($variable)")
            continue
        fi
        if ! grep -q "$variable" "$path"; then
            missing+=("$flag is passed but not read from $variable")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        for item in "${missing[@]}"; do
            fail "$target never appends $item; the setting is inert on macOS"
        done
        continue
    fi
    pass "$target honours all ${#tunables[@]} optional llama.cpp tunables"
done

if (( FAILURES > 0 )); then
    echo ""
    echo "[FAIL] macOS llama.cpp tunable contract"
    exit 1
fi

echo ""
echo "[PASS] macOS native launchers honour every documented llama.cpp tunable"
