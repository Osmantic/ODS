#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=installers/lib/node-runtime.sh
. "$ROOT/installers/lib/node-runtime.sh"

TEST_ROOT="$(mktemp -d)"
cleanup() {
    case "$TEST_ROOT" in /tmp/*|/var/tmp/*) rm -f -- "$TEST_ROOT/node" "$TEST_ROOT/npm"; rmdir -- "$TEST_ROOT" ;; esac
}
trap cleanup EXIT

cp /bin/true "$TEST_ROOT/npm"

# A non-Node executable must not be accepted merely because it exists.
if ! ods_linux_node_tools_available "$(command -v bash)" "$TEST_ROOT/npm"; then
    # bash does not implement Node's -p contract, so this must fail.
    printf '%s\n' 'PASS: non-Node executable rejected'
else
    printf '%s\n' 'FAIL: non-Node executable accepted' >&2
    exit 1
fi

cp "$ROOT/tests/fixtures/node-runtime/node22" "$TEST_ROOT/node"
chmod 0755 "$TEST_ROOT/node" "$TEST_ROOT/npm"
ods_linux_node_tools_available "$TEST_ROOT/node" "$TEST_ROOT/npm"
printf '%s\n' 'PASS: Linux Node 22 and npm accepted'

cp "$ROOT/tests/fixtures/node-runtime/node18" "$TEST_ROOT/node"
chmod 0755 "$TEST_ROOT/node"
if ods_linux_node_tools_available "$TEST_ROOT/node" "$TEST_ROOT/npm"; then
    printf '%s\n' 'FAIL: Node 18 accepted' >&2
    exit 1
fi
printf '%s\n' 'PASS: Node 18 rejected'

if ods_linux_node_tools_available /mnt/c/Program\ Files/nodejs/node.exe /mnt/c/Program\ Files/nodejs/npm; then
    printf '%s\n' 'FAIL: Windows-mounted Node tooling accepted' >&2
    exit 1
fi
printf '%s\n' 'PASS: Windows-mounted Node tooling rejected'
