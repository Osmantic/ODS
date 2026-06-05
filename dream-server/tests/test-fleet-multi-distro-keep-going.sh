#!/usr/bin/env bash
# Regression: tests/fleet-multi-distro.sh must NOT abort the entire matrix
# when a single distro's `docker pull` fails. A bare `docker pull` under the
# script's `set -euo pipefail` exits the loop on the first registry hiccup
# (transient IPv6 failure, mirror outage, etc.) and silently skips every
# distro that comes later in TARGETS. During a fleet run on 2026-05-23, an IPv6
# `network is unreachable` on archlinux skipped manjaro, cachyos, and
# opensuse — a class of failures the matrix exists to surface.
#
# This test asserts the pull failure is contained inside an `if !` guard
# that increments `fail`, appends to `failed`, and `continue`s the loop.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/tests/fleet-multi-distro.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"

# Pull the docker-pull block (between `if [[ "$PULL" == "true" ]]` and the
# matching `fi`) inside the per-distro loop, strip comments.
pull_block="$(awk '
    /if \[\[ "\$PULL" == "true" \]\]; then/ { in_block=1; next }
    in_block { print }
    in_block && /^[[:space:]]*fi[[:space:]]*$/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

[[ -n "$pull_block" ]] || fail "could not locate per-distro docker-pull block"

grep -qE 'if[[:space:]]+!.*docker pull' <<<"$pull_block" \
    || fail "docker pull must be wrapped in an \`if !\` guard so failures don't abort the matrix under set -e"
pass "docker pull failure is contained inside an if-guard"

grep -qE 'fail=\$\(\(fail \+ 1\)\)' <<<"$pull_block" \
    || fail "failed pull must increment the fail counter (otherwise the summary line lies)"
pass "failed pull increments fail counter"

grep -qF 'failed+=("$distro")' <<<"$pull_block" \
    || fail "failed pull must append the distro to the failed[] array (otherwise it's missing from the summary)"
pass "failed pull appends to failed[] array"

grep -qE '^[[:space:]]*continue[[:space:]]*$' <<<"$pull_block" \
    || fail "failed pull must \`continue\` so remaining distros still run (the bug this test catches)"
pass "failed pull continues the loop instead of aborting"

echo "[OK] fleet-multi-distro.sh keeps going on docker pull failures"
