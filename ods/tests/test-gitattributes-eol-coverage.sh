#!/usr/bin/env bash
# Regression: every tracked file must resolve an explicit eol attribute so a
# Windows checkout with core.autocrlf cannot turn LF blobs into CRLF worktrees.
# Unpinned types have already broken make test on Windows: systemd units
# (^TimeoutStopSec=15$ no longer matches '15\r'), cli-config.yaml.template, and
# every .bats suite were excluded from .gitattributes' per-extension list.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$ROOT_DIR" rev-parse --show-toplevel)"

fail() { echo "[FAIL] $*" >&2; exit 1; }

# 1. No tracked file may resolve eol=unspecified.
uncovered="$(git -C "$REPO_ROOT" ls-files \
    | git -C "$REPO_ROOT" check-attr --stdin eol \
    | sed -n 's/: eol: unspecified$//p' \
    | sed -n '1,20p')"
if [[ -n "$uncovered" ]]; then
    echo "[FAIL] no eol rule covers:" >&2
    echo "$uncovered" >&2
    fail "tracked files without an eol rule exist"
fi

# 2. Windows-native PowerShell stays CRLF; the override must survive the
#    catch-all.
eol="$(git -C "$REPO_ROOT" check-attr eol -- ods/installers/windows/install-windows.ps1 | sed 's/.*: //')"
[[ "$eol" == "crlf" ]] || fail "install-windows.ps1 must keep eol=crlf, got: $eol"

# 3. Previously unpinned text types that broke tests on Windows checkouts.
for path in \
    ods/scripts/systemd/ods-host-agent.service \
    ods/scripts/systemd/memory-shepherd-memory.timer \
    ods/extensions/services/hermes/cli-config.yaml.template \
    ods/tests/bats-tests/detection.bats \
    ods/extensions/services/dashboard-api/main.py \
    ods/Makefile \
    ods/ods-cli; do
    eol="$(git -C "$REPO_ROOT" check-attr eol -- "$path" | sed 's/.*: //')"
    [[ "$eol" == "lf" ]] || fail "$path resolves eol=$eol, expected lf"
done

echo "[PASS] every tracked file resolves an explicit eol rule; PowerShell stays CRLF, the rest LF"
