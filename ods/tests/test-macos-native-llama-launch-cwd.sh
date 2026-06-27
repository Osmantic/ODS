#!/usr/bin/env bash
# Regression: native macOS llama-server must launch from the install directory.
# llama.cpp probes its current directory while loading backends; if the detached
# upgrader inherits a removed cwd from a reinstall, it aborts before serving.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

assert_llama_exec_anchored() {
    local target="$1" label="$2"

    [[ -f "$target" ]] || fail "missing $target"

    awk '
        /cd "\$INSTALL_DIR" \|\| exit 1/ { cd_seen=3 }
        /exec "\$LLAMA_SERVER_BIN"/ {
            if (!cd_seen) exit 1
            exec_count++
        }
        { if (cd_seen > 0) cd_seen-- }
        END { if (exec_count == 0) exit 2 }
    ' "$target" || fail "$label: native llama-server exec must be immediately preceded by cd \"\$INSTALL_DIR\""

    pass "$label: native llama-server launches from INSTALL_DIR"
}

bootstrap="$ROOT_DIR/scripts/bootstrap-upgrade.sh"
installer="$ROOT_DIR/installers/macos/install-macos.sh"
cli="$ROOT_DIR/installers/macos/ods-macos.sh"

grep -qF 'cd "$INSTALL_DIR" || {' "$bootstrap" \
    || fail "bootstrap-upgrade.sh must anchor its own cwd to INSTALL_DIR"
pass "bootstrap-upgrade.sh anchors the detached upgrader cwd"

assert_llama_exec_anchored "$bootstrap" "bootstrap hot-swap"
assert_llama_exec_anchored "$installer" "macOS installer"
assert_llama_exec_anchored "$cli" "ods-macos restart"

grep -qF 'com.ods.llama-server' "$installer" \
    || fail "macOS installer must unload legacy llama-server LaunchAgent"
grep -qF 'com.ods.full-model-download' "$installer" \
    || fail "macOS installer must unload legacy full-model-download LaunchAgent"
pass "macOS installer clears legacy native llama LaunchAgents"

grep -qF 'Full model downloaded and verified, but native macOS llama-server did not load it after swap' "$bootstrap" \
    || fail "macOS native hot-swap failure must persist an honest failed status"
grep -qF 'write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES"' "$bootstrap" \
    || fail "macOS native hot-swap failure must report real downloaded bytes"
pass "macOS native hot-swap failure is reported as failed"

echo "[OK] macOS native llama launch cwd contract holds"
