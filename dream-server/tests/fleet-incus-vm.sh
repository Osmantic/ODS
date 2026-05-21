#!/usr/bin/env bash
# Run disposable Incus VMs to validate systemd + Docker-backed installer paths.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="$(date +%Y%m%d%H%M%S)-$$"

PREFIX="dream-vm"
CPU="2"
MEMORY="4GiB"
WAIT_TIMEOUT="600"
KEEP_VMS=false
RUN_INSTALLER_DRY_RUN=true
WORK_DIR=""

declare -a CREATED_VMS=()
declare -a TARGETS=()

declare -A IMAGES=(
    [ubuntu2404]="images:ubuntu/24.04"
    [fedora42]="images:fedora/42"
    [rocky9]="images:rockylinux/9"
    [arch]="images:archlinux/current"
    [opensuse]="images:opensuse/tumbleweed"
)

declare -A EXPECTED_PKG=(
    [ubuntu2404]="apt"
    [fedora42]="dnf"
    [rocky9]="dnf"
    [arch]="pacman"
    [opensuse]="zypper"
)

declare -A LABELS=(
    [ubuntu2404]="Ubuntu 24.04 LTS"
    [fedora42]="Fedora 42"
    [rocky9]="Rocky Linux 9"
    [arch]="Arch Linux current"
    [opensuse]="openSUSE Tumbleweed"
)

declare -A ALIASES=(
    [ubuntu]="ubuntu2404"
    [ubuntu24]="ubuntu2404"
    [ubuntu2404]="ubuntu2404"
    [ubuntu/24.04]="ubuntu2404"
    [fedora]="fedora42"
    [fedora42]="fedora42"
    [fedora/42]="fedora42"
    [rocky]="rocky9"
    [rocky9]="rocky9"
    [rockylinux/9]="rocky9"
    [arch]="arch"
    [archlinux]="arch"
    [archlinux/current]="arch"
    [opensuse]="opensuse"
    [tumbleweed]="opensuse"
    [opensuse/tumbleweed]="opensuse"
)

ORDER=(ubuntu2404 fedora42 rocky9 arch opensuse)

log() {
    printf '%s\n' "$*"
}

fail() {
    printf '[FAIL] %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
Usage: tests/fleet-incus-vm.sh [options] [distro...]

Run disposable Incus virtual machines that exercise a real systemd boot,
Docker daemon startup, package-manager detection, and an installer dry-run
without --skip-docker.

Options:
  --list                    List available VM lanes
  --keep-vms                Leave VMs running after the test for debugging
  --no-installer-dry-run    Skip the Dream Server installer dry-run
  --vm-prefix NAME          Prefix for disposable VM names (default: dream-vm)
  --cpu N                   vCPUs per VM (default: 2)
  --memory SIZE             Memory per VM (default: 4GiB)
  --timeout SECONDS         Wait timeout for VM agent readiness (default: 600)
  -h, --help                Show this help

Default matrix:
  ubuntu2404 fedora42 rocky9 arch opensuse

Aliases:
  ubuntu/24.04, fedora/42, rockylinux/9, archlinux/current,
  opensuse/tumbleweed
USAGE
}

list_lanes() {
    printf '%-12s %-28s %-28s %s\n' "ID" "Label" "Incus image" "Package manager"
    for lane in "${ORDER[@]}"; do
        printf '%-12s %-28s %-28s %s\n' "$lane" "${LABELS[$lane]}" "${IMAGES[$lane]}" "${EXPECTED_PKG[$lane]}"
    done
}

canonical_lane() {
    local raw="$1"
    if [[ -n "${IMAGES[$raw]:-}" ]]; then
        printf '%s\n' "$raw"
        return 0
    fi
    if [[ -n "${ALIASES[$raw]:-}" ]]; then
        printf '%s\n' "${ALIASES[$raw]}"
        return 0
    fi
    return 1
}

cleanup() {
    local vm
    if [[ "$KEEP_VMS" != "true" ]]; then
        for vm in "${CREATED_VMS[@]}"; do
            incus delete -f "$vm" >/dev/null 2>&1 || true
        done
    else
        if ((${#CREATED_VMS[@]} > 0)); then
            log ""
            log "Leaving VMs running for debugging:"
            printf '  %s\n' "${CREATED_VMS[@]}"
        fi
    fi

    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

while (($# > 0)); do
    case "$1" in
        --list)
            list_lanes
            exit 0
            ;;
        --keep-vms)
            KEEP_VMS=true
            shift
            ;;
        --no-installer-dry-run)
            RUN_INSTALLER_DRY_RUN=false
            shift
            ;;
        --vm-prefix)
            PREFIX="${2:?missing value for --vm-prefix}"
            shift 2
            ;;
        --cpu)
            CPU="${2:?missing value for --cpu}"
            shift 2
            ;;
        --memory)
            MEMORY="${2:?missing value for --memory}"
            shift 2
            ;;
        --timeout)
            WAIT_TIMEOUT="${2:?missing value for --timeout}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            fail "Unknown option: $1"
            ;;
        *)
            lane="$(canonical_lane "$1")" || fail "Unknown distro lane: $1"
            TARGETS+=("$lane")
            shift
            ;;
    esac
done

if ((${#TARGETS[@]} == 0)); then
    TARGETS=("${ORDER[@]}")
fi

command -v incus >/dev/null 2>&1 || fail "incus command not found"
incus info >/dev/null 2>&1 || fail "incus is not initialized or this user cannot access it"

WORK_DIR="$(mktemp -d)"
PAYLOAD="$WORK_DIR/dream-server-src.tgz"
VM_CHECK="$WORK_DIR/fleet-incus-vm-check.sh"

cat > "$VM_CHECK" <<'VM_CHECK_SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail

DISTRO_ID="${1:?missing distro id}"
EXPECTED_PKG="${2:?missing expected package manager}"
INSTALLER_MODE="${3:-run}"
SRC_DIR="/opt/dream-server-src"

info() {
    printf '[%s] %s\n' "$DISTRO_ID" "$*"
}

fail() {
    printf '[%s] [FAIL] %s\n' "$DISTRO_ID" "$*" >&2
    exit 1
}

is_systemd_ready() {
    local state
    command -v systemctl >/dev/null 2>&1 || return 1
    state="$(systemctl is-system-running 2>/dev/null || true)"
    case "$state" in
        running|degraded) return 0 ;;
        *) return 1 ;;
    esac
}

wait_for_systemd() {
    local deadline=$((SECONDS + 180))
    until is_systemd_ready; do
        if ((SECONDS >= deadline)); then
            systemctl is-system-running || true
            fail "systemd did not reach running/degraded state"
        fi
        sleep 3
    done
    info "systemd state: $(systemctl is-system-running 2>/dev/null || true)"
}

wait_for_network() {
    local deadline=$((SECONDS + 180))
    until ip -4 route show default >/dev/null 2>&1 && getent hosts archive.ubuntu.com >/dev/null 2>&1; do
        if ((SECONDS >= deadline)); then
            ip addr || true
            ip route || true
            resolvectl status || true
            fail "guest network did not get IPv4 egress and DNS"
        fi
        sleep 3
    done
    info "guest network has IPv4 egress and DNS"
}

install_apt_deps() {
    export DEBIAN_FRONTEND=noninteractive
    export NEEDRESTART_MODE=a
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        bash ca-certificates curl gawk git jq python3 python3-yaml rsync sudo tar
    if ! apt-get install -y --no-install-recommends docker.io docker-compose-v2; then
        apt-get install -y --no-install-recommends docker.io
    fi
}

install_dnf_deps() {
    local dnf_bin="dnf"
    local distro_id="unknown"
    if ! command -v dnf >/dev/null 2>&1; then
        dnf_bin="yum"
    fi
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        distro_id="${ID:-unknown}"
    fi

    "$dnf_bin" -y install \
        bash ca-certificates curl gawk git jq python3 python3-pyyaml rsync sudo tar

    if [[ "$distro_id" =~ ^(rocky|almalinux|rhel|ol|centos)$ ]]; then
        info "using Docker CE CentOS/RHEL repository for ${distro_id}"
        "$dnf_bin" -y install dnf-plugins-core
        rm -f /etc/yum.repos.d/docker-ce.repo /etc/yum.repos.d/docker-ce-staging.repo
        "$dnf_bin" config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        "$dnf_bin" makecache
        "$dnf_bin" -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
    elif ! "$dnf_bin" -y install moby-engine docker-compose-plugin; then
        info "native Docker packages unavailable; falling back to get.docker.com"
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        sh /tmp/get-docker.sh
    fi
}

install_pacman_deps() {
    pacman -Syu --noconfirm --needed \
        bash ca-certificates curl gawk git jq python python-yaml rsync sudo tar docker docker-compose
}

install_zypper_deps() {
    zypper --non-interactive refresh || true
    zypper --non-interactive install -y \
        bash ca-certificates curl gawk git jq python3 python3-PyYAML rsync sudo tar
    if ! zypper --non-interactive install -y docker docker-compose; then
        info "native Docker packages unavailable; falling back to get.docker.com"
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        sh /tmp/get-docker.sh
    fi
}

install_dependencies() {
    if command -v apt-get >/dev/null 2>&1; then
        install_apt_deps
    elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
        install_dnf_deps
    elif command -v pacman >/dev/null 2>&1; then
        install_pacman_deps
    elif command -v zypper >/dev/null 2>&1; then
        install_zypper_deps
    else
        fail "no supported package manager found"
    fi
}

enable_docker() {
    systemctl daemon-reload || true
    systemctl enable --now docker

    local deadline=$((SECONDS + 120))
    until docker info >/dev/null 2>&1; do
        if ((SECONDS >= deadline)); then
            systemctl status docker --no-pager || true
            fail "Docker daemon did not become ready"
        fi
        sleep 2
    done

    info "Docker daemon is active"
    docker version --format 'Docker server {{.Server.Version}}' || true
    docker compose version || info "Docker Compose plugin is not installed by the distro package"
}

extract_source() {
    rm -rf "$SRC_DIR"
    mkdir -p "$SRC_DIR"
    tar -xzf /tmp/dream-server-src.tgz -C "$SRC_DIR"
    useradd -m -s /bin/bash dreamtest >/dev/null 2>&1 || true
    printf 'dreamtest ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/dreamtest
    chmod 0440 /etc/sudoers.d/dreamtest
    if getent group docker >/dev/null 2>&1; then
        usermod -aG docker dreamtest
    fi
    chown -R dreamtest:dreamtest "$SRC_DIR"
}

check_package_detection() {
    local detected
    detected="$(
        cd "$SRC_DIR"
        bash -lc '
            log(){ :; }
            warn(){ printf "[warn] %s\n" "$*" >&2; }
            error(){ printf "[error] %s\n" "$*" >&2; return 1; }
            source installers/lib/packaging.sh
            detect_pkg_manager
            printf "%s\n" "$PKG_MANAGER"
        '
    )"
    if [[ "$detected" != "$EXPECTED_PKG" ]]; then
        fail "expected package manager $EXPECTED_PKG, got $detected"
    fi
    info "package manager detected as $detected"
}

check_scripts() {
    cd "$SRC_DIR"
    bash -n install-core.sh installers/lib/packaging.sh scripts/resolve-compose-stack.sh
    info "core shell syntax passed"
}

run_installer_dry_run() {
    if [[ "$INSTALLER_MODE" != "run" ]]; then
        info "skipping installer dry-run by request"
        return 0
    fi

    sudo -u dreamtest -H bash -lc '
        set -Eeuo pipefail
        cd /opt/dream-server-src
        export INSTALL_DIR="$HOME/dream-server-test"
        bash install-core.sh \
            --dry-run \
            --non-interactive \
            --force \
            --tier 1 \
            --no-comfyui \
            --no-voice \
            --no-workflows \
            --no-rag \
            --no-recommended \
            --no-hermes \
            --no-openclaw
    '
    info "installer dry-run completed with Docker enabled"
}

info "os-release: $(tr '\n' ' ' < /etc/os-release)"
wait_for_systemd
wait_for_network
install_dependencies
enable_docker
extract_source
check_package_detection
check_scripts
run_installer_dry_run
info "PASS"
VM_CHECK_SCRIPT

tar \
    --exclude='./.git' \
    --exclude='./node_modules' \
    --exclude='./data' \
    --exclude='./token-spy' \
    --exclude='./.pytest_cache' \
    --exclude='./__pycache__' \
    -C "$ROOT_DIR" \
    -czf "$PAYLOAD" \
    .

wait_for_exec() {
    local vm="$1"
    local deadline=$((SECONDS + WAIT_TIMEOUT))
    until incus exec "$vm" -- true >/dev/null 2>&1; do
        if ((SECONDS >= deadline)); then
            incus info "$vm" || true
            fail "$vm did not become reachable through the Incus agent"
        fi
        sleep 5
    done
}

run_lane() {
    local lane="$1"
    local vm="${PREFIX}-${lane}-${RUN_ID}"
    local installer_mode="run"

    if [[ "$RUN_INSTALLER_DRY_RUN" != "true" ]]; then
        installer_mode="skip"
    fi

    log ""
    log "=== ${LABELS[$lane]} (${IMAGES[$lane]}) ==="
    incus init "${IMAGES[$lane]}" "$vm" --vm \
        -c "limits.cpu=$CPU" \
        -c "limits.memory=$MEMORY" \
        -c "security.secureboot=false" \
        </dev/null
    CREATED_VMS+=("$vm")
    incus config device add "$vm" agent disk source=agent:config >/dev/null
    incus start "$vm"

    wait_for_exec "$vm"
    incus file push "$PAYLOAD" "$vm/tmp/dream-server-src.tgz"
    incus file push "$VM_CHECK" "$vm/tmp/fleet-incus-vm-check.sh"
    incus exec "$vm" -- chmod +x /tmp/fleet-incus-vm-check.sh
    incus exec "$vm" -- /tmp/fleet-incus-vm-check.sh "$lane" "${EXPECTED_PKG[$lane]}" "$installer_mode"

    if [[ "$KEEP_VMS" != "true" ]]; then
        incus delete -f "$vm" >/dev/null
    fi
}

for lane in "${TARGETS[@]}"; do
    run_lane "$lane"
done

log ""
log "Incus VM fleet matrix passed: ${TARGETS[*]}"
