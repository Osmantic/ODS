#!/usr/bin/env bash
# Phase 05 must install the Docker buildx plugin when Docker is present without
# it. Docker CE packages include buildx, but Arch, openSUSE, Void and Alpine ship
# it separately, and without it Compose builds ODS's local images with the
# classic builder (#7092). Source the real phase with the stub set the
# podman-shim suite uses and check which packages it requests; then check the
# real pkg_resolve names.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FAILED=0
fail() { echo "[FAIL] $*" >&2; FAILED=$((FAILED + 1)); }
pass() { echo "[PASS] $*"; }

if [[ "${BASH_VERSINFO[0]:-0}" -lt 4 ]]; then
    echo "[SKIP] phase 05 requires Bash 4+; this host only has Bash ${BASH_VERSION} (brew install bash)"
    exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

TOOLS="$tmp_dir/tools"; mkdir -p "$TOOLS"
for t in bash sh grep egrep sed awk head tail cut tr cat sort uniq wc mktemp rm mkdir touch chmod ls \
         dirname basename id uname sleep env date find xargs readlink realpath tee stat ps hostname python3; do
    p="$(command -v "$t" 2>/dev/null || true)"
    [[ -n "$p" ]] && ln -sf "$p" "$TOOLS/$t"
done

run_phase() {
    # $1 = docker flavour (docker | podman), $2 = buildx present (yes | no),
    # $3 = DRY_RUN, $4 = SKIP_DOCKER. Prints the phase log, then package calls.
    local flavour="$1" buildx="$2" dry_run="$3" skip="$4"
    local bin="$tmp_dir/bin" log="$tmp_dir/phase.log" calls="$tmp_dir/calls.log"
    rm -rf "$bin"; mkdir -p "$bin"; : > "$log"; : > "$calls"
    local version="Docker version 29.0.0"
    [[ "$flavour" == podman ]] && version="podman version 5.6.2"
    local buildx_rc=1
    [[ "$buildx" == yes ]] && buildx_rc=0
    cat > "$bin/docker" <<DOCKER
#!/usr/bin/env bash
case "\${1:-}" in
    --version) echo "$version" ;;
    buildx) exit $buildx_rc ;;
esac
exit 0
DOCKER
    chmod +x "$bin/docker"
    HARNESS_BIN="$bin" HARNESS_TOOLS="$TOOLS" HARNESS_LOG="$log" HARNESS_CALLS="$calls" \
    HARNESS_DRY="$dry_run" HARNESS_SKIP="$skip" HARNESS_ROOT="$ROOT_DIR" \
    bash -c '
set -uo pipefail
export PATH="$HARNESS_BIN:$HARNESS_TOOLS"
export SCRIPT_DIR="$HARNESS_ROOT" LOG_FILE="$HARNESS_LOG"
export DRY_RUN="$HARNESS_DRY" INTERACTIVE=false SKIP_DOCKER="$HARNESS_SKIP" GPU_COUNT=0 GPU_BACKEND=cpu
export DOCKER_CMD="" DOCKER_COMPOSE_CMD="" DOCKER_NEEDS_SUDO=false PKG_MANAGER=pacman
log() { echo "LOG: $1" >> "$LOG_FILE"; }
warn() { echo "WARN: $1" >> "$LOG_FILE"; }
error() { echo "ERROR: $1" >> "$LOG_FILE"; exit 1; }
ai() { echo "AI: $1" >> "$LOG_FILE"; }
ai_ok() { echo "OK: $1" >> "$LOG_FILE"; }
ai_bad() { :; }
ai_warn() { echo "AI_WARN: $1" >> "$LOG_FILE"; }
show_phase() { :; }
ods_progress() { :; }
detect_pkg_manager() { :; }
ods_sudo_available() { return 0; }
ods_sudo() { "$@"; }
pkg_update() { :; }
pkg_resolve() { echo "$1"; }
pkg_install() { echo "pkg_install $*" >> "$HARNESS_CALLS"; }
# The phase continues into daemon checks the stubs cannot satisfy; its exit
# status is not under test, the recorded calls are.
# shellcheck source=/dev/null
source "$SCRIPT_DIR/installers/phases/05-docker.sh" >/dev/null 2>&1 || true
'
    cat "$log"; echo "--- calls ---"; cat "$calls"
}

out="$(run_phase docker no false false)"
if grep -q '^pkg_install docker-buildx-plugin$' <<<"$out"; then
    pass "Docker without buildx: phase requests the buildx plugin"
else
    fail "Docker without buildx: buildx plugin not requested"; printf '%s\n' "$out" | tail -5 >&2
fi

out="$(run_phase docker yes false false)"
if grep -q 'docker-buildx-plugin' <<<"$out"; then
    fail "Docker with buildx: buildx requested again"
else
    pass "Docker with buildx: nothing installed"
fi

out="$(run_phase podman no false false)"
if grep -q 'docker-buildx-plugin' <<<"$out"; then
    fail "Podman's docker CLI: buildx requested"
else
    pass "Podman's docker CLI: buildx not requested"
fi

out="$(run_phase docker no false true)"
if grep -q 'docker-buildx-plugin' <<<"$out"; then
    fail "--skip-docker: buildx requested"
else
    pass "--skip-docker: no packages requested"
fi

out="$(run_phase docker no true false)"
if grep -q 'pkg_install docker-buildx-plugin' <<<"$out"; then
    fail "dry run: buildx installed"
elif grep -qF '[DRY RUN] Would install the Docker buildx plugin' <<<"$out"; then
    pass "dry run: buildx install only logged"
else
    fail "dry run: no buildx message"
fi

# Real distro package names.
for pair in apt:docker-buildx-plugin dnf:docker-buildx-plugin pacman:docker-buildx \
            zypper:docker-buildx xbps:docker-buildx apk:docker-cli-buildx; do
    manager="${pair%%:*}" expected="${pair#*:}"
    # packaging.sh resets PKG_MANAGER when sourced, so set it afterwards.
    got="$(bash -c '
        log() { :; }; warn() { :; }; error() { :; }
        # shellcheck source=/dev/null
        source "$1/installers/lib/packaging.sh"
        PKG_MANAGER="$2"
        pkg_resolve docker-buildx-plugin' _ "$ROOT_DIR" "$manager")"
    if [[ "$got" == "$expected" ]]; then
        pass "pkg_resolve docker-buildx-plugin on $manager is $expected"
    else
        fail "pkg_resolve docker-buildx-plugin on $manager is '$got', expected $expected"
    fi
done

[[ $FAILED -eq 0 ]] || { echo "$FAILED check(s) failed" >&2; exit 1; }
echo "All phase 05 buildx checks passed"
