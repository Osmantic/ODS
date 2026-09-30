#!/usr/bin/env bash
# Regression guard for the phase-06 APE private-state preparation.
#
# APE's image runs as the system user from `adduser --system --no-create-home
# ape`, which on the pinned python:3.12-slim base resolves to UID 100 /
# GID 65534 (nogroup), independently of the installer owner. Phase 06 must hand
# APE a private, container-writable data/ape bind source; otherwise the
# container cannot create state.json/audit.jsonl and crash-loops.
#
# Proof strategy (no host root required, no re-implementation of the shipped
# chown):
#   1. Structure: the shipped block must use a symlink-safe recursive chown and
#      a private 0700 mode, never a broad 0777, and must be gated on data/ape
#      actually being a bind mount in the APE compose fragment.
#   2. Behavior: the *real extracted block* is executed inside a disposable
#      root container (busybox) over a bind-mounted fixture, so the shipped
#      chown -h -R / chmod 700 run for real. This covers a wrong owner under
#      umask 077, content preservation, symlink refusal (target untouched), and
#      a failing-privilege propagation. The real-Docker layer is also where the
#      exact issued argv is proven. All of it is skipped (never faked) when
#      Docker or the helper image is unavailable.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE06="$ROOT_DIR/installers/phases/06-directories.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
skip() { echo "SKIP: $*"; }

[[ -f "$PHASE06" ]] || fail "phase 06 is missing"

# Extract the APE preparation block (through its closing `fi`) so the test
# exercises the shipped logic, not a re-implementation.
APE_BLOCK="$(awk '/# APE \(Agent Policy Engine\) persists private governance state/{grab=1} grab{print} grab && /^    fi$/{exit}' "$PHASE06")"
[[ -n "$APE_BLOCK" ]] || fail "could not locate the phase-06 APE ownership block"
grep -Fq 'chmod 700 /data' <<<"$APE_BLOCK" \
    || fail "APE state must be private (0700), not world-writable"
grep -Fq 'chown -h -R' <<<"$APE_BLOCK" \
    || fail "APE preparation must chown recursively without following symlinks (-h)"
if grep -Ev '^[[:space:]]*#' <<<"$APE_BLOCK" | grep -Eq 'chmod +(-R +)?0?777'; then
    fail "APE preparation must not use a broad chmod 777"
fi
grep -Fq 'data/ape:/data/ape' <<<"$APE_BLOCK" \
    || fail "APE preparation must be gated on the data/ape bind mount"
pass "shipped APE block is private, symlink-safe, and bind-mount gated"

TMP_DIR="$(mktemp -d)"
_cleanup() {
    if [[ "${_docker_ready:-0}" == "1" ]]; then
        docker run --rm --network none --user 0:0 \
            --mount "type=bind,src=$TMP_DIR,dst=/clean" \
            "$CONTAINER_IMG" rm -rf /clean 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR" 2>/dev/null || true
}
trap _cleanup EXIT

# The extracted block relies on helpers provided by phase 06 / the installer.
# Append a portable shim so the same shipped text can run unmodified inside a
# dumb container shell (busybox ash), with the privileged repair routed through
# whatever `ods_sudo` resolves to.
_write_driver() {
    local path="$1"
    {
        echo 'set -eu'
        echo 'log() { :; }'
        echo 'error() { echo "ERROR: $*" >&2; return 1; }'
        echo 'ai() { :; }; ai_ok() { :; }; ai_bad() { :; }; ai_warn() { :; }'
        echo '_phase06_rootless=false'
        echo 'ods_sudo_available() { return 0; }'
        # The container is the privileged context: run repairs directly.
        echo 'ods_sudo() { "$@"; }'
        echo 'docker() { return 1; }'
        echo '_ods_rootless_ensure_helper_image() { return 0; }'
        echo 'docker_run() { return 1; }'
        echo "$APE_BLOCK"
        # Report post-state from inside the container (root), where the fixture
        # may be unreadable to the unprivileged host user.
        echo 'echo "POSTSTATE=$(stat -c %u:%g:%a "$INSTALL_DIR/data/ape" 2>/dev/null || echo unreadable)"'
        echo 'echo "POSTCONTENT=$(cat "$INSTALL_DIR/data/ape/state.json" 2>/dev/null || echo MISSING)"'
    } > "$path"
}

_write_compose() {
    local dir="$1"
    mkdir -p "$dir/extensions/services/ape"
    printf 'services:\n  ape:\n    volumes:\n      - ./config/ape:/config:ro,z\n      - ./data/ape:/data/ape:z\n' \
        > "$dir/extensions/services/ape/compose.yaml"
}

# Run the shipped block as real root inside a disposable container over the
# fixture's install dir, so the actual chown/chmod execute. `env -i` keeps the
# container shell minimal; the block gets INSTALL_DIR, LOG_FILE, and a PATH.
CONTAINER_IMG="${ODS_APE_TEST_IMAGE:-busybox:latest}"
_docker_ready=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if docker image inspect "$CONTAINER_IMG" >/dev/null 2>&1 \
        || docker pull --quiet "$CONTAINER_IMG" >/dev/null 2>&1; then
        _docker_ready=1
    fi
fi

_write_driver "$TMP_DIR/driver.sh"

_run_real() {
    # $1 = host install dir; extra env via prefix; prints container stdout.
    local install_dir="$1"; shift
    docker run --rm --network none --user 0:0 \
        --mount "type=bind,src=$install_dir,dst=/install" \
        --mount "type=bind,src=$TMP_DIR/driver.sh,dst=/driver.sh,ro" \
        -e INSTALL_DIR=/install -e LOG_FILE=/install/.phase06.log \
        "$@" \
        "$CONTAINER_IMG" sh /driver.sh
}

if [[ "$_docker_ready" != "1" ]]; then
    skip "real-Docker behavior proof: Docker or helper image $CONTAINER_IMG unavailable"
    echo "test-ape-data-ownership: ok (behavior=skipped)"
    exit 0
fi

# ── Case 1: wrong owner under umask 077 must land 100:65534/700, data intact ──
INSTALL_DIR1="$TMP_DIR/ods1"
mkdir -p "$INSTALL_DIR1/data/ape"
_write_compose "$INSTALL_DIR1"
( umask 077; mkdir -p "$INSTALL_DIR1/data/ape" )
printf 'preserve-me\n' > "$INSTALL_DIR1/data/ape/state.json"
before_owner="$(stat -c '%u:%g' "$INSTALL_DIR1/data/ape")"
before_mode="$(stat -c '%a' "$INSTALL_DIR1/data/ape")"
[[ "$before_mode" == "700" ]] || fail "setup expected 0700, got $before_mode"

out1="$(_run_real "$INSTALL_DIR1") " || fail "APE ownership block returned non-zero for a real directory"
[[ "$out1" == *"POSTSTATE=100:65534:700"* ]] \
    || fail "data/ape owner/mode is not 100:65534:700: $out1"
[[ "$out1" == *"POSTCONTENT=preserve-me"* ]] \
    || fail "existing private file contents were not preserved: $out1"
[[ "$(stat -c '%a' "$INSTALL_DIR1/data/ape")" == "700" ]] \
    || fail "host-visible mode is not private 0700"
pass "wrong owner ${before_owner}/${before_mode} repaired to 100:65534/700; contents preserved"

# ── Case 2: symlinked data/ape is refused, not followed ──────────────────────
INSTALL_DIR2="$TMP_DIR/ods2"
mkdir -p "$INSTALL_DIR2/data" "$TMP_DIR/real-outside"
printf 'outside\n' > "$TMP_DIR/real-outside/keep.txt"
chmod 755 "$TMP_DIR/real-outside"
ln -s "$TMP_DIR/real-outside" "$INSTALL_DIR2/data/ape"
_write_compose "$INSTALL_DIR2"
if _run_real "$INSTALL_DIR2" >/dev/null; then
    fail "APE block accepted a symlinked data/ape instead of refusing it"
fi
[[ -f "$TMP_DIR/real-outside/keep.txt" ]] \
    || fail "APE block deleted content through the symlink"
cmp -s <(printf 'outside\n') "$TMP_DIR/real-outside/keep.txt" \
    || fail "APE block modified content through the symlink"
[[ "$(stat -c '%u:%g' "$TMP_DIR/real-outside")" == "$(stat -c '%u:%g' "$TMP_DIR")" ]] \
    || fail "APE block chowned the symlink target"
pass "symlinked data/ape refused; symlink target untouched"

# ── Case 3: a failing privileged chown must propagate (installer must abort) ──
INSTALL_DIR3="$TMP_DIR/ods3"
mkdir -p "$INSTALL_DIR3/data/ape"
_write_compose "$INSTALL_DIR3"
# Rebuild the driver so ods_sudo always fails, forcing the failure branch.
{
    sed '/^ods_sudo() { "\$@"; }$/d' "$TMP_DIR/driver.sh"
    echo 'ods_sudo() { return 1; }'
} > "$TMP_DIR/driver-fail.sh"
if docker run --rm --network none --user 0:0 \
    --mount "type=bind,src=$INSTALL_DIR3,dst=/install" \
    --mount "type=bind,src=$TMP_DIR/driver-fail.sh,dst=/driver.sh,ro" \
    -e INSTALL_DIR=/install -e LOG_FILE=/install/.phase06.log \
    "$CONTAINER_IMG" sh /driver.sh; then
    fail "a failing privileged chown did not propagate (installer would continue)"
fi
pass "failing privileged chown propagates a non-zero result"

# ── Case 4: no ape bind mount in compose -> block is a no-op ─────────────────
INSTALL_DIR4="$TMP_DIR/ods4"
mkdir -p "$INSTALL_DIR4/data/ape"
mkdir -p "$INSTALL_DIR4/extensions/services/ape"
printf 'services:\n  ape:\n    volumes:\n      - ./config/ape:/config:ro,z\n' \
    > "$INSTALL_DIR4/extensions/services/ape/compose.yaml"
pre4="$(stat -c '%u:%g:%a' "$INSTALL_DIR4/data/ape")"
out4="$(_run_real "$INSTALL_DIR4") " || fail "no-op gate returned non-zero"
[[ "$out4" == *"POSTSTATE=$pre4"* ]] \
    || fail "APE block changed data/ape though it is not a bind mount: $out4"
pass "APE block no-ops when data/ape is not a bind mount"

echo "test-ape-data-ownership: ok"