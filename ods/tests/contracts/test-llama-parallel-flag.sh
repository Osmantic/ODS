#!/usr/bin/env bash
# Contract: every llama.cpp launch path honours LLAMA_PARALLEL.
#
# LLAMA_PARALLEL is a documented .env tunable (".env.schema.json", ".env.example")
# and installers/phases/06-directories.sh writes it into every generated .env.
# llama.cpp defaults --parallel to 1, so a launcher that never passes the flag
# makes the setting inert on that platform only -- the operator changes the
# value, restarts, and nothing happens, with no error to explain why.
#
# Lemonade-backed paths are exempt: llama-server there is Lemonade's own
# `serve` subcommand, which takes its llama.cpp flags through --llamacpp-args.

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

# Compose replaces a command list wholesale rather than merging it, so each
# overlay that defines its own llama-server command needs the flag directly.
compose_targets=(
    docker-compose.base.yml
    docker-compose.cpu.yml
    docker-compose.arc.yml
    docker-compose.intel.yml
)

# Native launchers build the argv themselves.
native_targets=(
    bin/ods-host-agent.py
    installers/windows/install-windows.ps1
    installers/windows/ods.ps1
)

# Bash launchers append optional flags to a `llama_args` array. Matching that
# append form rather than a bare "--parallel" keeps the check honest for
# scripts/bootstrap-upgrade.sh, which also embeds a Windows PowerShell block
# that has always passed the flag -- a plain grep would pass on that alone and
# never notice the macOS block missing it.
bash_arg_array_targets=(
    installers/macos/install-macos.sh
    installers/macos/ods-macos.sh
    scripts/bootstrap-upgrade.sh
)

for target in "${bash_arg_array_targets[@]}"; do
    path="$ROOT_DIR/$target"
    if [[ ! -f "$path" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
        continue
    fi
    if ! grep -qE '_?llama_args\+=\(--parallel' "$path"; then
        fail "$target builds its llama_args array without --parallel; LLAMA_PARALLEL is inert on this platform"
        continue
    fi
    pass "$target honours LLAMA_PARALLEL"
done

for target in "${compose_targets[@]}" "${native_targets[@]}"; do
    path="$ROOT_DIR/$target"
    if [[ ! -f "$path" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
        continue
    fi
    if ! grep -q -- '--parallel' "$path"; then
        fail "$target starts llama-server without --parallel; LLAMA_PARALLEL is inert on this platform"
        continue
    fi
    if ! grep -q 'LLAMA_PARALLEL' "$path"; then
        fail "$target passes --parallel but not from LLAMA_PARALLEL, so the .env tunable is ignored"
        continue
    fi
    pass "$target honours LLAMA_PARALLEL"
done

if (( FAILURES > 0 )); then
    echo ""
    echo "[FAIL] LLAMA_PARALLEL contract ($FAILURES launcher(s) affected)"
    exit 1
fi

echo ""
echo "[PASS] every llama.cpp launch path honours LLAMA_PARALLEL"
