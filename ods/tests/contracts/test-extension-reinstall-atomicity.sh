#!/usr/bin/env bash
# Contract: extension library reinstall must not delete the live tree before
# staging succeeds. Regression for issue #4162 — early shutil.rmtree(dest)
# left operators with no extension when staging failed mid-copy.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXT_PY="$ROOT/extensions/services/dashboard-api/routers/extensions.py"

if [[ ! -f "$EXT_PY" ]]; then
    echo "[FAIL] missing extensions router: $EXT_PY"
    exit 1
fi

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

echo "[contract] extension reinstall keeps dest until staged copy commits"

# install_extension must not pre-delete a broken/partial tree before the lock.
if grep -q 'Cleaning up extension directory before retry' "$EXT_PY"; then
    fail "install_extension still deletes dest before locked reinstall"
fi
if awk '/^def install_extension\(/,/^def _rewrite_build_context\(/' "$EXT_PY" | grep -q 'shutil\.rmtree(dest)'; then
    fail "install_extension still calls shutil.rmtree(dest)"
fi
pass "install_extension does not pre-delete dest"

# _install_from_library must stage first, then swap with os.replace (not early rmtree).
install_fn="$(awk '/^def _install_from_library\(/,/^def _rewrite_build_context\(/' "$EXT_PY")"
if printf '%s\n' "$install_fn" | grep -q 'shutil\.rmtree(dest)'; then
    fail "_install_from_library still calls shutil.rmtree(dest)"
fi
if ! printf '%s\n' "$install_fn" | grep -q 'with _staged_library_extension'; then
    fail "_install_from_library lost staged library copy path"
fi
if ! printf '%s\n' "$install_fn" | grep -q 'os\.replace(staged, dest)'; then
    fail "_install_from_library must commit staged copy with os.replace(staged, dest)"
fi
if ! printf '%s\n' "$install_fn" | grep -q 'os\.replace(dest, prior)'; then
    fail "_install_from_library must move prior tree aside with os.replace(dest, prior)"
fi
if ! printf '%s\n' "$install_fn" | grep -q 'os\.replace(prior, dest)'; then
    fail "_install_from_library must restore prior tree when staged commit fails"
fi
pass "_install_from_library uses stage-then-replace swap"

echo "[contract] extension reinstall atomicity — all checks passed"
