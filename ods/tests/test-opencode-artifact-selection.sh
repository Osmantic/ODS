#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/lib/verified-download.sh"
stage=$(mktemp -d /tmp/ods-opencode-selection.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

uname() {
    case "$1" in -s) printf '%s\n' "$test_platform" ;; -m) printf '%s\n' "$test_arch" ;; *) return 1 ;; esac
}
ldd() {
    [[ "$1" == --version ]] || return 1
    printf '%s\n' "$test_libc"
    # musl ldd may exit nonzero even when its version output identifies musl.
    [[ "$test_libc" != musl ]]
}
sysctl() {
    [[ "$*" == '-n sysctl.proc_translated' ]] || return 1
    [[ "$test_rosetta" != unavailable ]] || return 1
    printf '%s\n' "$test_rosetta"
}
check() {
    local expected="$1" actual asset digest
    actual=$(ods_opencode_artifact) || fail "selection failed: $test_platform/$test_arch/$test_libc"
    read -r asset digest <<< "$actual"
    [[ "$asset" == "$expected" && "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "wrong artifact: $actual"
}

test_platform=Linux test_arch=x86_64 test_libc=glibc test_rosetta=unavailable
check opencode-linux-x64-baseline.tar.gz
test_libc=musl
check opencode-linux-x64-baseline-musl.tar.gz
for test_arch in arm64 aarch64; do
    test_libc=glibc
    check opencode-linux-arm64.tar.gz
    test_libc=musl
    check opencode-linux-arm64-musl.tar.gz
done
test_platform=Darwin test_arch=x86_64 test_libc=glibc
check opencode-darwin-x64-baseline.zip
test_rosetta=0
check opencode-darwin-x64-baseline.zip
test_rosetta=1
check opencode-darwin-arm64.zip
test_arch=arm64 test_rosetta=unavailable
check opencode-darwin-arm64.zip

# Unsupported targets must stop before requesting bytes or creating an install.
test_platform=Linux test_arch=riscv64
ods_download_verified() { printf 'unexpected download\n' > "$stage/network"; return 1; }
if ods_install_verified_opencode 2>/dev/null; then fail 'unsupported target accepted'; fi
[[ ! -e "$stage/network" ]] || fail 'unsupported target requested an archive'
printf '[PASS] 10 artifact selections preserve baseline x64, libc, ARM aliases and Rosetta; unsupported targets stop before download\n'
