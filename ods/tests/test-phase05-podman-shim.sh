#!/usr/bin/env bash
# Phase 05 must put a podman-only host on the supported Podman path: install
# Podman's docker-compatible CLI (podman-docker) instead of Docker CE, and a
# compose provider Podman can delegate to (podman-compose) instead of
# docker-compose-plugin, which only exists in Docker's repositories.
#
# ODS drives Podman through the podman-docker shim (see #2804 and
# scripts/linux-install-preflight.sh). Fedora ships podman and podman-docker as
# separate packages, so a podman-only host used to fall into the "Installing
# Docker..." branch. Source the real phase with the stub set the Docker-phase
# BATS suite uses, on a PATH that holds only base utilities plus the runtimes
# each case declares (the host's own docker/podman must stay invisible), and
# check which branches run and which packages get requested.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/05-docker.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$PHASE" ]] || fail "missing phase 05: $PHASE"

# Static guard: the podman arm must sit between the docker-found arm and the
# Docker CE install branch.
docker_line="$(grep -n 'elif command -v docker &> /dev/null; then' "$PHASE" | head -1 | cut -d: -f1)"
podman_line="$(grep -n 'elif command -v podman &> /dev/null; then' "$PHASE" | head -1 | cut -d: -f1)"
install_line="$(grep -n 'ods_progress 31 "docker" "Installing Docker engine"' "$PHASE" | head -1 | cut -d: -f1)"
[[ -n "$docker_line" && -n "$podman_line" && -n "$install_line" \
    && "$docker_line" -lt "$podman_line" && "$podman_line" -lt "$install_line" ]] \
    || fail "phase 05 must check for podman after docker and before the Docker CE install branch"
pass "podman arm sits between the docker-found arm and the Docker CE install branch"

if [[ "${BASH_VERSINFO[0]:-0}" -lt 4 ]]; then
    echo "[SKIP] phase 05 requires Bash 4+; this host only has Bash ${BASH_VERSION} (brew install bash)"
    exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# Base utilities only: a symlink farm so the host's real docker/podman are
# never on PATH unless a case adds a fake one.
TOOLS="$tmp_dir/tools"; mkdir -p "$TOOLS"
for t in bash sh grep egrep sed awk head tail cut tr cat sort uniq wc mktemp rm mkdir touch chmod ls \
         dirname basename id uname sleep env date find xargs readlink realpath tee stat ps hostname python3; do
    p="$(command -v "$t" 2>/dev/null || true)"
    [[ -n "$p" ]] && ln -sf "$p" "$TOOLS/$t"
done

run_phase() {
    # $1 = runtimes to provide on PATH (space separated: podman, docker, none)
    # $2 = DRY_RUN value, $3 = package manager id. Prints the phase's log,
    # then the recorded package/network calls.
    local tools="$1" dry_run="$2" pkg="$3"
    local bin="$tmp_dir/bin" log="$tmp_dir/phase.log" calls="$tmp_dir/calls.log"
    rm -rf "$bin" "$log" "$calls"; mkdir -p "$bin"; : > "$log"; : > "$calls"
    for t in $tools; do
        case "$t" in
            podman)
                printf '#!/usr/bin/env bash\ncase "${1:-}" in --version) echo "podman version 5.6.2";; version) echo "Client: Podman Engine";; info) echo "{}";; esac\nexit 0\n' > "$bin/podman"
                chmod +x "$bin/podman" ;;
            docker)
                # A real Docker CLI: compose is already available.
                printf '#!/usr/bin/env bash\ncase "${1:-}" in --version) echo "Docker version 29.0.0";; esac\nexit 0\n' > "$bin/docker"
                chmod +x "$bin/docker" ;;
        esac
    done
    # Any attempt to fetch get.docker.com is a Docker CE install attempt.
    printf '#!/usr/bin/env bash\necho "DOCKER_CE_INSTALL_ATTEMPTED" >> "%s"\nexit 1\n' "$calls" > "$bin/curl"; chmod +x "$bin/curl"
    HARNESS_BIN="$bin" HARNESS_TOOLS="$TOOLS" HARNESS_LOG="$log" HARNESS_CALLS="$calls" HARNESS_DRY="$dry_run" HARNESS_PKG="$pkg" HARNESS_ROOT="$ROOT_DIR" \
    bash -c '
set -uo pipefail
export PATH="$HARNESS_BIN:$HARNESS_TOOLS"
export SCRIPT_DIR="$HARNESS_ROOT" LOG_FILE="$HARNESS_LOG"
export DRY_RUN="$HARNESS_DRY" INTERACTIVE=false SKIP_DOCKER=false GPU_COUNT=0 GPU_BACKEND=cpu
export DOCKER_CMD="" DOCKER_COMPOSE_CMD="" DOCKER_NEEDS_SUDO=false PKG_MANAGER="$HARNESS_PKG"
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
pkg_install() {
    echo "pkg_install $*" >> "$HARNESS_CALLS"
    # Installing the shim makes a docker CLI appear (as podman-docker does);
    # its compose subcommand only works once a provider is installed.
    if [[ " $* " == *" podman-docker "* ]]; then
        cat > "$HARNESS_BIN/docker" <<DOCKER
#!/usr/bin/env bash
case "\${1:-}" in
    --version) echo "podman version 5.6.2" ;;
    compose) [[ -f "$HARNESS_BIN/.compose-provider" ]] || exit 1 ;;
esac
exit 0
DOCKER
        chmod +x "$HARNESS_BIN/docker"
    fi
    if [[ " $* " == *" podman-compose "* ]]; then
        : > "$HARNESS_BIN/.compose-provider"
    fi
}
# shellcheck source=/dev/null
source "$SCRIPT_DIR/installers/phases/05-docker.sh" >/dev/null 2>&1 || true
'
    cat "$log"; echo "--- calls ---"; cat "$calls"
}

# Sanity: with no runtimes declared, neither docker nor podman may be visible.
out="$(run_phase "" true apt)"
grep -q 'Docker already installed' <<<"$out" && fail "harness leaks the host docker onto PATH; got: $out"

# Case 1: podman only, dry run -> announces the shim and podman-compose, not Docker CE.
out="$(run_phase "podman" true dnf)"
grep -q 'Would install podman-docker' <<<"$out" || fail "dry run on a podman-only host must announce the podman-docker shim; got: $out"
grep -q 'Would install Docker via official script' <<<"$out" && fail "dry run on a podman-only host must not plan a Docker CE install"
grep -q 'Would install podman-compose' <<<"$out" || fail "dry run on a podman-only host must plan podman-compose; got: $out"
grep -q 'Would install Docker Compose plugin' <<<"$out" && fail "dry run on a podman-only host must not plan docker-compose-plugin"
pass "dry run on a podman-only host plans podman-docker and podman-compose, not Docker CE"

# Case 2: podman only, real run -> installs the shim and podman-compose, never fetches get.docker.com.
out="$(run_phase "podman" false dnf)"
grep -q 'pkg_install podman-docker' <<<"$out" || fail "real run on a podman-only host must install podman-docker; got: $out"
grep -q 'DOCKER_CE_INSTALL_ATTEMPTED' <<<"$out" && fail "real run on a podman-only host must not attempt a Docker CE install"
grep -q 'Podman docker-compatible CLI installed' <<<"$out" || fail "real run must confirm the shim once docker resolves; got: $out"
grep -q 'pkg_install podman-compose' <<<"$out" || fail "real run on a podman-only host must install podman-compose; got: $out"
grep -q 'pkg_install docker-compose-plugin' <<<"$out" && fail "real run on a podman-only host must not install docker-compose-plugin"
pass "real run on a podman-only host installs podman-docker and podman-compose, skipping the Docker CE path"

# Case 3 (unchanged): nothing installed, dry run -> Docker CE plan as before.
out="$(run_phase "" true apt)"
grep -q 'Would install Docker via official script' <<<"$out" || fail "hosts without podman must keep the Docker CE plan; got: $out"
grep -q 'podman' <<<"$out" && fail "hosts without podman must not mention podman packages"
pass "hosts with neither runtime keep the Docker CE install plan"

# Case 4 (unchanged): docker present -> nothing installed, compose untouched.
out="$(run_phase "docker" true apt)"
grep -q 'Docker already installed' <<<"$out" || fail "hosts with docker must report it as already installed; got: $out"
grep -q 'Would install' <<<"$out" && fail "hosts with a working docker CLI must not plan any install; got: $out"
pass "hosts with a docker CLI keep the already-installed path"
