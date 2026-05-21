#!/usr/bin/env bash
# Dream Server fleet multi-distro runner.
#
# This is the noninteractive distro gate intended for fleet hosts such as
# tower2. It uses Docker directly instead of Distrobox so it can run from SSH,
# cron, or a fleet harness without pre-created per-distro containers.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

declare -A IMAGES=(
    [ubuntu2404]="ubuntu:24.04"
    [ubuntu2204]="ubuntu:22.04"
    [debian12]="debian:12"
    [mint213]="linuxmintd/mint21.3-amd64:latest"
    [fedora41]="fedora:41"
    [rocky9]="rockylinux:9"
    [arch]="archlinux:latest"
    [manjaro]="manjarolinux/base:latest"
    [cachyos]="cachyos/cachyos:latest"
    [opensuse]="opensuse/tumbleweed:latest"
)

declare -A EXPECTED_PKG=(
    [ubuntu2404]="apt"
    [ubuntu2204]="apt"
    [debian12]="apt"
    [mint213]="apt"
    [fedora41]="dnf"
    [rocky9]="dnf"
    [arch]="pacman"
    [manjaro]="pacman"
    [cachyos]="pacman"
    [opensuse]="zypper"
)

declare -A ALIASES=(
    [ubuntu/24.04]="ubuntu2404"
    [ubuntu-24.04]="ubuntu2404"
    [ubuntu/22.04]="ubuntu2204"
    [ubuntu-22.04]="ubuntu2204"
    [debian/12]="debian12"
    [debian-12]="debian12"
    [linuxmint]="mint213"
    [linuxmint/21.3]="mint213"
    [mint]="mint213"
    [mint/21.3]="mint213"
    [fedora/41]="fedora41"
    [fedora-41]="fedora41"
    [rocky]="rocky9"
    [rockylinux]="rocky9"
    [rockylinux/9]="rocky9"
    [rocky/9]="rocky9"
    [archlinux]="arch"
    [archlinux/current]="arch"
    [manjaro/current]="manjaro"
    [cachyos/current]="cachyos"
    [cachyOS]="cachyos"
    [opensuse/tumbleweed]="opensuse"
    [opensuse-tumbleweed]="opensuse"
    [tumbleweed]="opensuse"
)

ORDER=(ubuntu2404 ubuntu2204 debian12 mint213 fedora41 rocky9 arch manjaro cachyos opensuse)

PULL=false
RUN_DRY_RUN=true
KEEP_WORK=false
TARGETS=()

usage() {
    cat <<'EOF'
Usage: tests/fleet-multi-distro.sh [options] [distro...]

Run Dream Server distro compatibility checks in disposable Docker containers.

Options:
  --pull          Pull/refresh distro images before running.
  --no-dry-run    Skip install-core.sh --dry-run inside each distro.
  --keep-work     Keep temporary host work directory for debugging.
  --list          List supported distro IDs and images.
  -h, --help      Show this help.

Examples:
  tests/fleet-multi-distro.sh --pull
  tests/fleet-multi-distro.sh ubuntu/24.04 archlinux/current mint
  tests/fleet-multi-distro.sh --no-dry-run ubuntu2404
EOF
}

list_distros() {
    printf '%-12s %-36s %s\n' "ID" "IMAGE" "PACKAGE_MANAGER"
    for distro in "${ORDER[@]}"; do
        printf '%-12s %-36s %s\n' "$distro" "${IMAGES[$distro]}" "${EXPECTED_PKG[$distro]}"
    done
}

normalize_distro() {
    local distro="$1"
    printf '%s\n' "${ALIASES[$distro]:-$distro}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pull)
            PULL=true
            shift
            ;;
        --no-dry-run)
            RUN_DRY_RUN=false
            shift
            ;;
        --keep-work)
            KEEP_WORK=true
            shift
            ;;
        --list)
            list_distros
            exit 0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                TARGETS+=("$1")
                shift
            done
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            TARGETS+=("$1")
            shift
            ;;
    esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    TARGETS=("${ORDER[@]}")
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is required for fleet multi-distro tests." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon is not reachable." >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dream-fleet-distro.XXXXXX")"
cleanup() {
    if [[ "$KEEP_WORK" == "true" ]]; then
        echo "Keeping work dir: $WORK_DIR"
    else
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

cat > "$WORK_DIR/bootstrap.sh" <<'EOF'
#!/bin/sh
set -eu

install_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bash ca-certificates curl gawk git jq python3 python3-yaml rsync sudo
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q bash ca-certificates curl-minimal gawk git jq python3 python3-pyyaml rsync sudo
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Syu --noconfirm --needed bash ca-certificates curl gawk git jq python python-yaml rsync sudo
    elif command -v zypper >/dev/null 2>&1; then
        zypper --non-interactive refresh
        zypper --non-interactive install -y bash ca-certificates curl gawk git jq python3 python3-PyYAML rsync sudo
    else
        echo "ERROR: no supported package manager found in container" >&2
        exit 1
    fi
}

install_deps

if ! id dreamtest >/dev/null 2>&1; then
    useradd -m -s /bin/bash dreamtest
fi
mkdir -p /etc/sudoers.d
echo 'dreamtest ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dreamtest
chmod 0440 /etc/sudoers.d/dreamtest

exec bash /fleet/check.sh "$@"
EOF

cat > "$WORK_DIR/check.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

name="$1"
expected_pkg="$2"
run_dry_run="$3"

cd /work

echo "=== $name: os-release ==="
cat /etc/os-release

export LOG_FILE=/dev/null
log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

source installers/lib/packaging.sh
detect_pkg_manager
echo "Detected package manager: $PKG_MANAGER"
if [[ "$PKG_MANAGER" != "$expected_pkg" ]]; then
    echo "FAIL: expected $expected_pkg, got $PKG_MANAGER" >&2
    exit 1
fi

pkg_install curl git jq >/tmp/pkg-install.log 2>&1 || {
    cat /tmp/pkg-install.log >&2
    exit 1
}

bash -n install-core.sh
bash -n installers/lib/packaging.sh
bash -n scripts/resolve-compose-stack.sh

flags="$(bash scripts/resolve-compose-stack.sh --script-dir "$PWD" --tier 1 --gpu-backend cpu)"
echo "Compose flags: $flags"
grep -q -- "-f docker-compose.base.yml" <<<"$flags"

if [[ "$run_dry_run" == "true" ]]; then
    echo "=== $name: installer dry-run ==="
    if command -v runuser >/dev/null 2>&1; then
        dry_run_cmd=(runuser -u dreamtest -- bash -lc 'cd /work && bash install-core.sh --dry-run --non-interactive --skip-docker --force')
    else
        dry_run_cmd=(su dreamtest -s /bin/bash -c 'cd /work && bash install-core.sh --dry-run --non-interactive --skip-docker --force')
    fi
    if ! "${dry_run_cmd[@]}" >/tmp/install-dry-run.log 2>&1; then
        tail -80 /tmp/install-dry-run.log >&2
        exit 1
    fi
fi

echo "PASS: $name"
EOF

chmod +x "$WORK_DIR/bootstrap.sh" "$WORK_DIR/check.sh"

pass=0
fail=0
failed=()

for distro in "${TARGETS[@]}"; do
    distro="$(normalize_distro "$distro")"
    image="${IMAGES[$distro]:-}"
    expected="${EXPECTED_PKG[$distro]:-}"
    if [[ -z "$image" || -z "$expected" ]]; then
        echo "FAIL: unknown distro '$distro' (use --list)" >&2
        failed+=("$distro")
        fail=$((fail + 1))
        continue
    fi

    echo ""
    echo "=== Fleet distro: $distro ($image) ==="
    if [[ "$PULL" == "true" ]]; then
        docker pull "$image"
    fi

    container_name="dream-fleet-${distro}-$$"
    if docker run --rm \
        --name "$container_name" \
        -v "$ROOT_DIR:/work:ro" \
        -v "$WORK_DIR:/fleet:ro" \
        -w /work \
        "$image" \
        sh /fleet/bootstrap.sh "$distro" "$expected" "$RUN_DRY_RUN"; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        failed+=("$distro")
    fi
done

echo ""
echo "=== Fleet multi-distro summary ==="
echo "Passed: $pass"
echo "Failed: $fail"
if [[ ${#failed[@]} -gt 0 ]]; then
    printf 'Failed distros: %s\n' "${failed[*]}"
    exit 1
fi
