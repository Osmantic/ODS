#!/usr/bin/env bash
# Exercise failure propagation without invoking any package manager.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib/packaging.sh"
warn() { :; }
sleep() { waits=$((waits + 1)); }
PKG_RETRY_ATTEMPTS=3
PKG_RETRY_DELAY=0
calls=0 waits=0
_pkg_run() { calls=$((calls + 1)); return 47; }
if _pkg_retry unavailable-package; then
    echo 'FAIL: exhausted package failures were reported as success' >&2
    exit 1
else
    result=$?
fi
[[ "$result" == 47 && "$calls" == 3 && "$waits" == 2 ]]
echo 'PASS: retries stop at the bound and preserve the real exit status'
calls=0 waits=0
_pkg_run() { calls=$((calls + 1)); [[ "$calls" == 2 ]]; }
_pkg_retry transient-package
[[ "$calls" == 2 && "$waits" == 1 ]]
echo 'PASS: successful retry returns immediately'
calls=0 waits=0
_pkg_run() { calls=$((calls + 1)); return 0; }
_pkg_retry available-package
[[ "$calls" == 1 && "$waits" == 0 ]]
echo 'PASS: initial success does not retry'
