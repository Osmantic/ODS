#!/usr/bin/env bash
# Validate that local Markdown links in key docs resolve to existing files.
#
# Scope (intentionally minimal):
#  - repo root README.md
#  - dream-server/*.md
#  - dream-server/docs/**/*.md
#
# This catches broken relative links caused by file moves/renames.
# External URLs and purely in-page anchors are ignored.
#
# Run from repo root:  bash dream-server/tests/test-doc-links.sh
# Or from dream-server: bash tests/test-doc-links.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# If invoked from dream-server/ (ROOT_DIR), also check the top-level README.
# If invoked from repo root, ROOT_DIR points to dream-server/ already.
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

# Return 0 if the target should be treated as a local filesystem path.
is_local_target() {
  local target="$1"

  # Strip whitespace
  target="${target## }"
  target="${target%% }"

  [[ -z "$target" ]] && return 1

  # in-page anchors
  [[ "$target" == \#* ]] && return 1

  # common non-file link types
  [[ "$target" == mailto:* ]] && return 1

  # URI schemes (http, https, etc.)
  if [[ "$target" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*:// ]]; then
    return 1
  fi

  return 0
}

# Normalize a markdown link target into a filesystem path.
# - removes #anchor and ?query
# - resolves relative to the source file dir
resolve_target_path() {
  local from_file="$1"
  local target="$2"

  target="${target%%#*}"
  target="${target%%\?*}"

  # ignore absolute paths (repo-relative paths are usually written as relative)
  if [[ "$target" == /* ]]; then
    echo ""
    return 0
  fi

  local from_dir
  from_dir="$(cd "$(dirname "$from_file")" && pwd)"

  python3 - "$from_dir" "$target" <<'PY'
import os, sys
from_dir = sys.argv[1]
target = sys.argv[2]
print(os.path.normpath(os.path.join(from_dir, target)))
PY
}

check_markdown_file() {
  local file="$1"

  # Extract markdown link targets of the form: [text](target)
  # This is intentionally simple; it aims to catch typical relative file links.
  local line
  local lineno=0
  while IFS= read -r line; do
    lineno=$((lineno + 1))

    # Allow multiple links per line.
    # shellcheck disable=SC2001
    local re='\[[^]]*\]\(([^)]*)\)'
    while [[ "$line" =~ $re ]]; do
      local target="${BASH_REMATCH[1]}"
      local rest="${line#*"${BASH_REMATCH[0]}"}"

      if is_local_target "$target"; then
        local resolved
        resolved="$(resolve_target_path "$file" "$target")"
        if [[ -n "$resolved" && ! -e "$resolved" ]]; then
          echo "[FAIL] $file:$lineno -> $target"
          echo "       resolved: ${resolved#$REPO_ROOT/}"
          return 1
        fi
      fi

      line="$rest"
    done
  done < "$file"
}

main() {
  local failures=0

  local files_to_check=()

  # 1) top-level README (exists in repo root)
  if [[ -f "$REPO_ROOT/README.md" ]]; then
    files_to_check+=("$REPO_ROOT/README.md")
  fi

  # 2) dream-server/*.md
  while IFS= read -r -d '' f; do
    files_to_check+=("$f")
  done < <(find "$ROOT_DIR" -maxdepth 1 -type f -name '*.md' -print0)

  # 3) dream-server/docs/**/*.md
  while IFS= read -r -d '' f; do
    files_to_check+=("$f")
  done < <(find "$ROOT_DIR/docs" -type f -name '*.md' -print0)

  [[ ${#files_to_check[@]} -gt 0 ]] || fail "No markdown files found to check"

  for f in "${files_to_check[@]}"; do
    if ! check_markdown_file "$f"; then
      failures=$((failures + 1))
    fi
  done

  if [[ $failures -gt 0 ]]; then
    fail "$failures markdown file(s) contain broken local links"
  fi

  pass "No broken local links found in repo README, dream-server root docs, and dream-server/docs"
}

main "$@"
