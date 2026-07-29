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
native_targets=(
    bin/ods-host-agent.py
    installers/macos/install-macos.sh
    installers/macos/ods-macos.sh
    scripts/bootstrap-upgrade.sh
    installers/windows/install-windows.ps1
    installers/windows/ods.ps1
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

if (( FAILURES > 0 )); then
    echo ""
    echo "[FAIL] llama.cpp --metrics contract ($FAILURES launcher(s) affected)"
    exit 1
fi

echo ""
echo "[PASS] every llama-server launch path enables --metrics"
