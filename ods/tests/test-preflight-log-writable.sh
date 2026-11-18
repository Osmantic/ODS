#!/usr/bin/env bash
# Regression: ods-preflight.sh must name the unwritable install dir instead of
# dying on a bare redirect under `set -e` (#2930).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# root ignores the write bits, so the unwritable-dir condition cannot be staged.
if [ "$(id -u)" -eq 0 ]; then
    echo "SKIP: must run as a non-root user to stage an unwritable directory"
    exit 0
fi

tmp="$(mktemp -d)"
trap 'chmod u+w "$tmp/install" 2>/dev/null; rm -rf "$tmp"' EXIT

# A stand-in install dir holding only the preflight script and the libs it
# sources before the log is opened, then made read-only.
mkdir -p "$tmp/install/lib"
cp "$ROOT/ods-preflight.sh" "$tmp/install/"
# Copy the whole lib/ rather than naming files: ods-preflight.sh sources
# several of them before it opens the log, and that list grows (it gained
# lib/preflight-llm-route.sh upstream, which broke a fixture that cherry-picked).
cp -R "$ROOT/lib/." "$tmp/install/lib/"
chmod a-w "$tmp/install"

set +e
out="$(bash "$tmp/install/ods-preflight.sh" 2>&1)"
rc=$?
set -e

fail=0
if [ "$rc" -eq 0 ]; then
    echo "FAIL: expected a non-zero exit on an unwritable install dir, got 0"
    fail=1
fi
case "$out" in
    *"cannot create the preflight log"*) echo "PASS: names the failure" ;;
    *) echo "FAIL: no actionable error; output was: $out"; fail=1 ;;
esac
case "$out" in
    *"$tmp/install"*) echo "PASS: names the install dir" ;;
    *) echo "FAIL: error does not name the path; output was: $out"; fail=1 ;;
esac

[ "$fail" -eq 0 ] || exit 1
echo "All preflight log-writability tests passed"
