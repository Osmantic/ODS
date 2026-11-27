#!/usr/bin/env bash
# Regression: Linux/WSL preflight's Docker Desktop probe must give up instead
# of waiting forever when the engine never answers (Linux/WSL twin of #8498).

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
preflight="$root_dir/installers/phases/01-preflight.sh"
tmp_dir="$(mktemp -d)"
bin_dir="$tmp_dir/bin"
hung_seconds=1337
# Only the hung stub sleeps this long; pkill may find nothing to stop.
trap 'pkill -f "sleep $hung_seconds" 2>/dev/null || true; rm -rf "$tmp_dir"' EXIT
mkdir -p "$bin_dir"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

# The phase runs on source; load only the function under test.
sed -n '/^check_docker_desktop_sharing() {/,/^}/p' "$preflight" > "$tmp_dir/probe.sh"
grep -q 'timeout' "$tmp_dir/probe.sh" || fail "check_docker_desktop_sharing has no bounded docker info"

run_probe() {
    PATH="$bin_dir:$PATH" ODS_DOCKER_PROBE_TIMEOUT_SECONDS=2 LOG_FILE="$tmp_dir/log" bash -c '
        set -euo pipefail
        error() { printf "[ERROR] %s\n" "$1"; exit 1; }
        log() { :; }
        source "$1"
        check_docker_desktop_sharing
        echo "probe returned"
    ' _ "$tmp_dir/probe.sh"
}

printf '#!/bin/sh\nexec sleep %s\n' "$hung_seconds" > "$bin_dir/docker"
chmod +x "$bin_dir/docker"
start=$SECONDS
if out="$(run_probe 2>&1)"; then
    fail "hung engine: preflight continued; output: $out"
fi
(( SECONDS - start <= 10 )) || fail "hung engine: preflight took $((SECONDS - start))s"
grep -q "Docker is not responding" <<< "$out" || fail "hung engine: no 'not responding' error; output: $out"
sleep 0.5
! pgrep -f "sleep $hung_seconds" >/dev/null || fail "hung engine: docker info left running"
printf '[PASS] hung engine: preflight stops with "Docker is not responding"\n'

printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$bin_dir/docker"
out="$(run_probe 2>&1)" || fail "stopped engine: probe failed; output: $out"
grep -q "probe returned" <<< "$out" || fail "stopped engine: probe did not return"
printf '[PASS] stopped engine: probe skips as before\n'

printf '#!/bin/sh\necho "\\"Ubuntu 24.04.1 LTS\\""\n' > "$bin_dir/docker"
out="$(run_probe 2>&1)" || fail "native engine: probe failed; output: $out"
grep -q "probe returned" <<< "$out" || fail "native engine: probe did not return"
printf '[PASS] native engine: not Docker Desktop, probe skips\n'
