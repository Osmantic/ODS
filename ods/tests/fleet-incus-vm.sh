#!/usr/bin/env bash
# Run disposable Incus VMs to validate systemd + Docker-backed installer paths.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="$(date +%Y%m%d%H%M%S)-$$"

PREFIX="ods-vm"
CPU="2"
MEMORY="4GiB"
WAIT_TIMEOUT="600"
KEEP_VMS=false
RUN_INSTALLER_DRY_RUN=true
HOST_LOCK=true
LOCK_FILE="${ODS_FLEET_HOST_LOCK:-/tmp/ods-fleet-heavy.lock}"
LOCK_TIMEOUT="${ODS_FLEET_HOST_LOCK_TIMEOUT_SECONDS:-}"
WORK_DIR=""

declare -a CREATED_VMS=()
declare -a TARGETS=()

declare -A IMAGES=(
    [ubuntu2404]="images:ubuntu/24.04"
    [fedora42]="images:almalinux/10"
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
    [fedora42]="AlmaLinux 10 dnf VM"
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
  --no-installer-dry-run    Skip the ODS installer dry-run
  --vm-prefix NAME          Prefix for disposable VM names (default: ods-vm)
  --cpu N                   vCPUs per VM (default: 2)
  --memory SIZE             Memory per VM (default: 4GiB)
  --timeout SECONDS         Wait timeout for VM agent readiness (default: 600)
  --lock-file PATH          Host lock path for coordinating with full fleet runs
                            (default: ODS_FLEET_HOST_LOCK or
                             /tmp/ods-fleet-heavy.lock)
  --lock-timeout SECONDS    Seconds to wait for the host lock before failing
                            (default: wait indefinitely)
  --no-host-lock            Do not take the shared host lock
  -h, --help                Show this help

Default matrix:
  ubuntu2404 fedora42 rocky9 arch opensuse
  (fedora42 currently uses an AlmaLinux dnf VM because the Incus public
   image remote no longer advertises Fedora VM aliases; Fedora remains
   covered by tests/fleet-multi-distro.sh container breadth.)

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

acquire_host_lock() {
    if [[ "$HOST_LOCK" != "true" ]]; then
        return 0
    fi

    if ! command -v flock >/dev/null 2>&1; then
        log "WARN: flock is unavailable; running without host-level contention guard."
        return 0
    fi

    local lock_dir
    lock_dir="$(dirname "$LOCK_FILE")"
    mkdir -p "$lock_dir"

    exec 9>"$LOCK_FILE"
    log "Acquiring fleet host lock: $LOCK_FILE"
    if [[ -n "$LOCK_TIMEOUT" ]]; then
        if ! flock -w "$LOCK_TIMEOUT" 9; then
            fail "Timed out waiting for fleet host lock: $LOCK_FILE"
        fi
    else
        flock 9
    fi
    log "Acquired fleet host lock: $LOCK_FILE"
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
        --lock-file)
            LOCK_FILE="${2:?missing value for --lock-file}"
            shift 2
            ;;
        --lock-timeout)
            LOCK_TIMEOUT="${2:?missing value for --lock-timeout}"
            shift 2
            ;;
        --no-host-lock)
            HOST_LOCK=false
            shift
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

acquire_host_lock

WORK_DIR="$(mktemp -d)"
PAYLOAD="$WORK_DIR/ods-src.tgz"
VM_CHECK="$WORK_DIR/fleet-incus-vm-check.sh"

cat > "$VM_CHECK" <<'VM_CHECK_SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail

DISTRO_ID="${1:?missing distro id}"
EXPECTED_PKG="${2:?missing expected package manager}"
INSTALLER_MODE="${3:-run}"
SRC_DIR="/opt/ods-src"

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

reboot_if_kernel_modules_changed() {
    local running_kernel
    running_kernel="$(uname -r)"
    if [[ -d "/usr/lib/modules/$running_kernel" ]]; then
        return 0
    fi

    local marker="/var/tmp/ods-fleet-incus-kernel-reboot-requested"
    if [[ -f "$marker" ]]; then
        fail "kernel modules for running kernel $running_kernel are still unavailable after reboot"
    fi

    info "kernel modules for running kernel $running_kernel are unavailable after package updates; rebooting before Docker"
    touch "$marker"
    systemctl reboot --no-block
    exit 75
}

extract_source() {
    rm -rf "$SRC_DIR"
    mkdir -p "$SRC_DIR"
    tar -xzf /tmp/ods-src.tgz -C "$SRC_DIR"
    useradd -m -s /bin/bash odstest >/dev/null 2>&1 || true
    printf 'odstest ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/odstest
    chmod 0440 /etc/sudoers.d/odstest
    if getent group docker >/dev/null 2>&1; then
        usermod -aG docker odstest
    fi
    chown -R odstest:odstest "$SRC_DIR"
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

    sudo -u odstest -H bash -lc '
        set -Eeuo pipefail
        cd /opt/ods-src
        export INSTALL_DIR="$HOME/ods-test"
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
reboot_if_kernel_modules_changed
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

vm_boot_id() {
    local vm="$1"
    incus exec "$vm" -- cat /proc/sys/kernel/random/boot_id 2>/dev/null || true
}

wait_for_reboot() {
    local vm="$1" old_boot_id="$2"
    local deadline=$((SECONDS + WAIT_TIMEOUT))
    local new_boot_id=""

    sleep 5
    while ((SECONDS < deadline)); do
        new_boot_id="$(vm_boot_id "$vm")"
        if [[ -n "$new_boot_id" && "$new_boot_id" != "$old_boot_id" ]]; then
            wait_for_exec "$vm"
            return 0
        fi
        sleep 5
    done

    incus info "$vm" || true
    fail "$vm did not complete requested reboot"
}

run_vm_check() {
    local vm="$1" lane="$2" installer_mode="$3"
    local boot_id rc
    boot_id="$(vm_boot_id "$vm")"
    if incus exec "$vm" -- /tmp/fleet-incus-vm-check.sh "$lane" "${EXPECTED_PKG[$lane]}" "$installer_mode"; then
        return 0
    fi
    rc=$?
    if [[ "$rc" -eq 75 ]]; then
        log "=== ${LABELS[$lane]} requested reboot after package updates ==="
        wait_for_reboot "$vm" "$boot_id"
        incus exec "$vm" -- /tmp/fleet-incus-vm-check.sh "$lane" "${EXPECTED_PKG[$lane]}" "$installer_mode"
        return $?
    fi
    return "$rc"
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
    incus file push "$PAYLOAD" "$vm/tmp/ods-src.tgz"
    incus file push "$VM_CHECK" "$vm/tmp/fleet-incus-vm-check.sh"
    incus exec "$vm" -- chmod +x /tmp/fleet-incus-vm-check.sh
    run_vm_check "$vm" "$lane" "$installer_mode"

    if [[ "$KEEP_VMS" != "true" ]]; then
        incus delete -f "$vm" >/dev/null
    fi
}

for lane in "${TARGETS[@]}"; do
    run_lane "$lane"
done

log ""
log "Incus VM fleet matrix passed: ${TARGETS[*]}"
