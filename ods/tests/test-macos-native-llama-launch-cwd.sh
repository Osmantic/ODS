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

grep -qF 'lsof -a -p "$pid" -d cwd -Fn' "$installer" \
    || fail "macOS installer must inspect cwd for relative native llama-server launches"
grep -qF './bin/llama-server*|bin/llama-server*' "$installer" \
    || fail "macOS installer must treat relative install-dir llama-server processes as owned"
grep -qF '"$process_cwd" == "$INSTALL_DIR"' "$installer" \
    || fail "macOS installer must reap owned relative-path native llama processes"
pass "macOS installer reaps owned relative-path native llama processes"

bridge="$ROOT_DIR/bin/ods-macos-llm-bridge.py"
bridge_manager="$ROOT_DIR/installers/macos/lib/bridge-manager.sh"
env_generator="$ROOT_DIR/installers/macos/lib/env-generator.sh"
uninstaller="$ROOT_DIR/ods-uninstall.sh"
[[ -f "$bridge" ]] || fail "macOS Colima LLM bridge script is missing"
[[ -f "$bridge_manager" ]] || fail "shared macOS bridge manager is missing"
grep -qF 'ODS_MACOS_LLM_BRIDGE_ENABLED=${macos_llm_bridge_enabled}' "$env_generator" \
    || fail "macOS env generator must enable the private LLM bridge for Colima"
grep -qF 'ODS_NATIVE_LLAMA_PORT=${native_llama_port}' "$env_generator" \
    || fail "macOS env generator must persist the native loopback LLM port"
grep -qF 'LLM_BACKEND=llama-server' "$env_generator" \
    || fail "macOS env generator must declare the native llama-server backend"
grep -qF 'upsert_env_value "${INSTALL_DIR}/.env" "LLM_BACKEND" "llama-server"' "$installer" \
    || fail "macOS local reruns must heal the native llama-server backend marker"
grep -qF 'ODS_MACOS_HOST_GATEWAY=${macos_host_gateway}' "$env_generator" \
    || fail "macOS env generator must persist the private Colima host gateway"
grep -qF 'ODS_MACOS_VM_IP=${macos_vm_ip}' "$env_generator" \
    || fail "macOS env generator must persist the authorized Colima VM peer"
grep -qF 'colima start --network-address --network-preferred-route' "$installer" \
    || fail "macOS installer must prefer Colima private vmnet routing"
grep -qF '_configure_macos_llm_bridge' "$installer" \
    || fail "macOS installer must launch the Colima LLM bridge before native llama"
grep -qF '_configure_macos_host_agent_bridge' "$installer" \
    || fail "macOS installer must bridge dashboard actions to the loopback host agent"
grep -qF 'source "${LIB_DIR}/bridge-manager.sh"' "$installer" \
    || fail "macOS installer must source the shared bridge manager"
grep -qF 'source "${LIB_DIR}/bridge-manager.sh"' "$cli" \
    || fail "macOS CLI must source the shared bridge manager"
grep -qF 'macos_configure_llm_bridge_from_env' "$bridge_manager" \
    || fail "shared bridge manager must derive LLM bridge state from .env"
grep -qF -- '--allow-peer' "$bridge_manager" \
    || fail "shared bridge manager must scope host bridges to the Colima VM peer"
grep -qF 'ODS_NATIVE_LLAMA_PORT' "$bootstrap" \
    || fail "bootstrap hot-swap must preserve the private native llama port"
grep -qF 'ODS_NATIVE_LLAMA_PORT' "$cli" \
    || fail "macOS CLI must preserve the private native llama port"
grep -qF 'com.ods.llm-bridge' "$uninstaller" \
    || fail "uninstaller must remove the Colima LLM bridge LaunchAgent"
grep -qF 'com.ods.host-agent-bridge' "$uninstaller" \
    || fail "uninstaller must remove the Colima host-agent bridge LaunchAgent"
pass "macOS Colima bridge lifecycle is wired through install, swap, CLI, and uninstall"

grep -qF 'Full model downloaded and verified, but native macOS llama-server did not load it after swap' "$bootstrap" \
    || fail "macOS native hot-swap failure must persist an honest failed status"
grep -qF 'write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES"' "$bootstrap" \
    || fail "macOS native hot-swap failure must report real downloaded bytes"
pass "macOS native hot-swap failure is reported as failed"

echo "[OK] macOS native llama launch cwd contract holds"
