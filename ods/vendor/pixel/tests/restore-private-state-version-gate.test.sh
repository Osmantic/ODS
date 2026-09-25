#!/usr/bin/env bash
# Negative coverage for the restore-private-state VERSION identity gate. The gate must read
# $ROOT/VERSION with a bounded dependency-free no-follow stable read (not `tr`), so a linked
# or swapped VERSION cannot pass the exact hardcoded 4.3.27 gate. We exercise the shared
# secure helper (scripts/lib/common.sh::pixel_read_release_version) that restore-private-state
# uses, against symlink/hardlink/oversize VERSION files, and statically prove the script no
# longer pipes VERSION through `tr`.
# The snippets below are intentionally literal search patterns for the script under test.
# shellcheck disable=SC2016
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SOURCE/scripts/lib/common.sh"
SCRIPT="$SOURCE/scripts/restore-private-state.sh"

fail() { echo "restore-private-state version-gate regression: $*" >&2; exit 1; }

# --- Static proof: the gate no longer reads VERSION through `tr` (bounded no-follow read). ---
if grep -n 'tr -d' "$SCRIPT" | grep -F 'VERSION' >/dev/null; then
  fail "restore-private-state still reads VERSION via tr"
fi
grep -F 'pixel_read_release_version "$ROOT"' "$SCRIPT" >/dev/null \
  || fail "restore-private-state gate does not use the secure release-version helper"

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

# Valid bounded single-link VERSION reads back trimmed content.
root="$tmp/valid"; mkdir -p "$root"
printf '4.3.9\n' > "$root/VERSION"
got=$(pixel_read_release_version "$root")
[[ "$got" == "4.3.9" ]] || fail "valid VERSION did not read back 4.3.9 (got '$got')"

# Symlink VERSION must fail closed.
root="$tmp/sym"; mkdir -p "$root"
printf '4.3.9\n' > "$tmp/sym-target"
ln -s "$tmp/sym-target" "$root/VERSION"
if pixel_read_release_version "$root" >/dev/null 2>&1; then
  fail "symlinked VERSION passed the gate"
fi

# Hardlink VERSION (nlink==2) must fail closed.
root="$tmp/hardlink"; mkdir -p "$root"
printf '4.3.9\n' > "$tmp/hardlink-original"
ln "$tmp/hardlink-original" "$root/VERSION"
if pixel_read_release_version "$root" >/dev/null 2>&1; then
  fail "hardlinked VERSION passed the gate"
fi

# Oversize VERSION (>64 bytes) must fail closed.
root="$tmp/oversize"; mkdir -p "$root"
python3 - "$root/VERSION" <<'PY'
import sys
open(sys.argv[1], "w", encoding="utf-8").write("4.3.9" + "x" * 80 + "\n")
PY
if pixel_read_release_version "$root" >/dev/null 2>&1; then
  fail "oversize VERSION passed the gate"
fi

# Internal whitespace/newline inside VERSION must fail closed: the old `tr -d`/split-join
# normalization must not turn an internally-split `4.\n3.0` into a passable `4.3.9`.
root="$tmp/innerspace"; mkdir -p "$root"
printf '4.\n3.0\n' > "$root/VERSION"
if pixel_read_release_version "$root" >/dev/null 2>&1; then
  fail "internally-split VERSION passed the gate"
fi

echo "restore-private-state version-gate regression passed"
