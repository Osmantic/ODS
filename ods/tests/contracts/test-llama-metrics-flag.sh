#!/usr/bin/env bash
# Contract: every llama-server launch path enables the Prometheus endpoint.
#
# llama.cpp serves /metrics only when started with --metrics. Two dashboard
# features scrape it: get_llama_metrics() in dashboard-api/helpers.py (the
# tokens/sec reading) and _extract_llama_cpp_prometheus_counters() in
# routers/usage.py (the Usage page's local-runtime counters). A launcher that
# omits the flag leaves both reading zero on that platform only, which is
# indistinguishable from an idle server.

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

# Compose overlays that define their own llama-server command. Compose replaces
# the command list rather than merging it, so each one needs its own --metrics.
compose_targets=(
    docker-compose.base.yml
    docker-compose.cpu.yml
    docker-compose.arc.yml
    docker-compose.intel.yml
)

# Native launchers: no compose involved, the flag is passed on the argv.
# Native Windows is checked below.
native_targets=(
    bin/ods-host-agent.py
    installers/macos/install-macos.sh
    installers/macos/ods-macos.sh
    scripts/bootstrap-upgrade.sh
)

for target in "${compose_targets[@]}" "${native_targets[@]}"; do
    path="$ROOT_DIR/$target"
    if [[ ! -f "$path" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
        continue
    fi
    if grep -q -- '--metrics' "$path"; then
        pass "$target enables the llama.cpp Prometheus endpoint"
    else
        fail "$target starts llama-server without --metrics; /metrics stays off and the dashboard reads 0 tok/s on this platform"
    fi
done

# Native Windows builds every llama-server.exe argv in installers/windows/lib.
# New-ODSNativeLlamaLaunchArguments is the pinned launch: the Portal task runs
# it through Invoke-ODSNativeLlamaRuntime, and a legacy installation's
# "ods.ps1 native-llm-start/-restart" through New-ODSNativeLlamaLegacyLaunch,
# which also builds a registered model-store profile's own argv. The installer
# starts the legacy runtime through its logon task, which runs ods.ps1.
win_runtime=installers/windows/lib/native-llama-runtime.ps1
win_legacy=installers/windows/lib/native-llama-legacy.ps1

# A top-level PowerShell function: its "function <Name>" line through the
# closing brace in column 0, the layout of the Windows sources.
ps_function() {
    awk -v name="$2" '
        $0 ~ ("^function " name "[ ({]") { inside = 1 }
        inside { print }
        inside && /^}/ { exit }
    ' "$ROOT_DIR/$1"
}

check_windows() {
    local file="$1" function="$2" needle="$3" label="$4" body
    if [[ ! -f "$ROOT_DIR/$file" ]]; then
        fail "$file is missing; update this contract if the Windows launch moved"
        return
    fi
    body="$(ps_function "$file" "$function")"
    if grep -Fq -- "$needle" <<<"$body"; then
        pass "$function in $file: $label"
    else
        fail "$function in $file lacks $needle, so this no longer holds: $label"
    fi
}

check_windows "$win_runtime" New-ODSNativeLlamaLaunchArguments "'--metrics'" \
    "the pinned Windows launch enables the llama.cpp Prometheus endpoint"
check_windows "$win_runtime" Invoke-ODSNativeLlamaRuntime 'New-ODSNativeLlamaLaunchArguments $Plan $Options' \
    "the Portal task starts llama-server with the pinned launch"
check_windows "$win_legacy" New-ODSNativeLlamaLegacyLaunch 'New-ODSNativeLlamaLaunchArguments $plan $launchOptions' \
    "a legacy installation starts the pinned llama-server with the pinned launch"
check_windows "$win_legacy" New-ODSNativeLlamaLegacyLaunch "'--metrics'" \
    "a registered model-store profile's launch enables the llama.cpp Prometheus endpoint"
if grep -Fq 'New-ODSNativeLlamaLegacyLaunch' "$ROOT_DIR/installers/windows/ods.ps1" \
    && grep -Fq 'Invoke-ODSNativeLlamaLegacyCutover' "$ROOT_DIR/installers/windows/install-windows.ps1"; then
    pass "installers/windows/ods.ps1 and install-windows.ps1 start llama-server through the shared Windows launch"
else
    fail "installers/windows/ods.ps1 and install-windows.ps1 must start llama-server through New-ODSNativeLlamaLegacyLaunch and Invoke-ODSNativeLlamaLegacyCutover"
fi
for target in installers/windows/ods.ps1 installers/windows/install-windows.ps1 installers/windows/lib/wsl-portal-amd.ps1; do
    if [[ ! -f "$ROOT_DIR/$target" ]]; then
        fail "$target is missing; update this contract if the launcher moved"
    elif grep -q -- '--ctx-size' "$ROOT_DIR/$target"; then
        fail "$target builds its own llama-server argv; launch through native-llama-runtime.ps1 or native-llama-legacy.ps1, which pass --metrics"
    else
        pass "$target builds no llama-server argv of its own"
    fi
done

if (( FAILURES > 0 )); then
    echo ""
    echo "[FAIL] llama.cpp --metrics contract ($FAILURES launcher(s) affected)"
    exit 1
fi

echo ""
echo "[PASS] every llama-server launch path enables --metrics"
