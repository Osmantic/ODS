#!/usr/bin/env bash
# Regression: macOS Docker probes must give up instead of waiting forever when
# the Docker socket accepts but the engine never answers (Docker Desktop stuck
# starting, a paused Colima/OrbStack VM). macOS twin of the Windows fix #8498.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
detection="$root_dir/installers/macos/lib/detection.sh"
macos_cli="$root_dir/installers/macos/ods-macos.sh"
tmp_dir="$(mktemp -d)"
bin_dir="$tmp_dir/bin"
hung_seconds=1337
# Only the hung stub sleeps this long; pkill may find nothing to stop.
trap 'pkill -f "sleep $hung_seconds" 2>/dev/null || true; rm -rf "$tmp_dir"' EXIT
mkdir -p "$bin_dir"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

set_docker() {
    case "$1" in
        hung) cat > "$bin_dir/docker" <<EOF
#!/bin/sh
case "\$1" in
  context) [ "\$2" = show ] && echo desktop-linux || echo unix:///tmp/docker.sock ;;
  *) exec sleep $hung_seconds ;;
esac
EOF
        ;;
        stopped) printf '#!/bin/sh\n[ "$1" = context ] && exit 0\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$bin_dir/docker" ;;
        healthy) printf '#!/bin/sh\ncase "$1" in\n  context) exit 0 ;;\n  version) echo 27.3.1 ;;\n  info) echo 8 ;;\nesac\n' > "$bin_dir/docker" ;;
    esac
    chmod +x "$bin_dir/docker"
}

probe() {
    PATH="$bin_dir:$PATH" ODS_DOCKER_PROBE_TIMEOUT_SECONDS=2 bash -c '
        set -euo pipefail
        source "$1"
        test_docker_desktop
        printf "%s %s %s\n" "$DOCKER_RUNNING" "$DOCKER_NOT_RESPONDING" "$DOCKER_VERSION"
    ' _ "$detection"
}

set_docker hung
start=$SECONDS
read -r running not_responding _ < <(probe)
(( SECONDS - start <= 10 )) || fail "hung engine: test_docker_desktop took $((SECONDS - start))s"
[[ "$running" == false ]] || fail "hung engine reported as running"
[[ "$not_responding" == true ]] || fail "hung engine not reported as not responding"
sleep 0.5
! pgrep -f "sleep $hung_seconds" >/dev/null || fail "hung engine: probe left its docker process running"
printf '[PASS] hung engine: probe stops after the timeout and reports not responding\n'

set_docker stopped
read -r running not_responding _ < <(probe)
[[ "$running" == false && "$not_responding" == false ]] || fail "stopped engine: got running=$running not_responding=$not_responding"
printf '[PASS] stopped engine: fails fast, not as a timeout\n'

set_docker healthy
start=$SECONDS
read -r running not_responding version < <(probe)
(( SECONDS - start <= 2 )) || fail "healthy engine: probe took $((SECONDS - start))s"
[[ "$running" == true && "$not_responding" == false && "$version" == 27.3.1 ]] \
    || fail "healthy engine: got running=$running not_responding=$not_responding version=$version"
printf '[PASS] healthy engine: running, version read, no delay\n'

# The CLI's own Docker check (start/stop/restart/update/status).
install_dir="$tmp_dir/install"
mkdir -p "$install_dir"
touch "$install_dir/docker-compose.base.yml" "$install_dir/.env"
set_docker hung
start=$SECONDS
if out="$(PATH="$bin_dir:$PATH" ODS_HOME="$install_dir" NO_COLOR=1 ODS_DOCKER_PROBE_TIMEOUT_SECONDS=2 \
    bash "$macos_cli" status 2>&1)"; then
    fail "ods-macos.sh status succeeded with a hung engine"
fi
(( SECONDS - start <= 10 )) || fail "ods-macos.sh status took $((SECONDS - start))s with a hung engine"
grep -q "not responding" <<< "$out" || { printf '%s\n' "$out" >&2; fail "ods-macos.sh did not say Docker is not responding"; }
printf '[PASS] ods-macos.sh stops with "not responding" instead of hanging\n'
