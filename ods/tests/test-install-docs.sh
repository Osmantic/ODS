#!/usr/bin/env bash
# Keep public install commands and provenance guidance aligned.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"

CANONICAL_ENDPOINT="https://install.osmantic.com/ods.sh"
CANONICAL_REPO_URL="https://github.com/Osmantic/ODS.git"
STABLE_VERSION="$(
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"]["version"])' \
        "$ROOT_DIR/manifest.json"
)"
STABLE_TAG="v$STABLE_VERSION"

fail() {
    echo "[FAIL] $*"
    exit 1
}

pass() {
    echo "[PASS] $*"
}

require_literal() {
    local file="$1"
    local literal="$2"
    local description="$3"

    grep -qF -- "$literal" "$file" \
        || fail "$description missing from ${file#"$REPO_ROOT"/}"
}

assert_no_retired_names() {
    python3 - "$REPO_ROOT" <<'PY'
import base64
import subprocess
import sys

repo_root = sys.argv[1]
patterns = [
    base64.b64decode("ZHJlYW0=").decode("ascii"),
    (
        base64.b64decode("bGlnaHQ=").decode("ascii")
        + r"[[:space:]_.-]*"
        + base64.b64decode("aGVhcnQ=").decode("ascii")
    ),
]

matches = []
for pattern in patterns:
    result = subprocess.run(
        ["git", "-C", repo_root, "grep", "-n", "-I", "-i", "-E", pattern, "--", "."],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    if result.stdout:
        matches.append(result.stdout.rstrip())

if matches:
    print("[FAIL] Retired product or organization references remain:")
    print("\n".join(matches))
    raise SystemExit(1)
PY
}

install_docs=(
    "$REPO_ROOT/README.md"
    "$ROOT_DIR/README.md"
    "$ROOT_DIR/QUICKSTART.md"
    "$ROOT_DIR/docs/FAQ.md"
    "$ROOT_DIR/docs/INSTALLER_TRUST.md"
    "$ROOT_DIR/get-ods.sh"
)

clone_docs=(
    "$REPO_ROOT/README.md"
    "$ROOT_DIR/README.md"
    "$ROOT_DIR/QUICKSTART.md"
    "$ROOT_DIR/docs/INSTALLER_TRUST.md"
)

for file in "${install_docs[@]}"; do
    [[ -f "$file" ]] || fail "Expected install document missing: $file"
    require_literal "$file" "$CANONICAL_ENDPOINT" "Canonical install endpoint"
done

for file in "${clone_docs[@]}"; do
    require_literal "$file" "$CANONICAL_REPO_URL" "Canonical clone URL"
done

compatible_ref_docs=(
    "$REPO_ROOT/README.md"
    "$ROOT_DIR/README.md"
    "$ROOT_DIR/QUICKSTART.md"
    "$ROOT_DIR/docs/FAQ.md"
)

for file in "${compatible_ref_docs[@]}"; do
    require_literal "$file" 'compatible ref with `ODS_REF`' "Compatible bootstrap ref guidance"
done

assert_no_retired_names

trust_doc="$ROOT_DIR/docs/INSTALLER_TRUST.md"
release_doc="$ROOT_DIR/docs/RELEASE_CHANNELS.md"
require_literal "$trust_doc" 'currently `main`' "Default branch guidance"
require_literal "$trust_doc" 'ODS_REF=' "Release-tag pinning guidance"
require_literal "$trust_doc" 'git checkout AUDITED_COMMIT_SHA' "Exact-commit guidance"
require_literal "$trust_doc" 'not a separate stable release channel' "Hosted-versus-raw channel guidance"
require_literal "$REPO_ROOT/README.md" "\`$STABLE_TAG\` is the current stable release" "README stable release"
require_literal "$release_doc" "current stable release is \`$STABLE_TAG\`" "Release channel stable release"
require_literal "$trust_doc" "--branch $STABLE_TAG $CANONICAL_REPO_URL" "Manual stable clone"
require_literal "$trust_doc" 'predates that repository layout' "Stable layout guidance"

if grep -qF "ODS_REF=$STABLE_TAG" "$REPO_ROOT/README.md" "$trust_doc"; then
    fail "$STABLE_TAG must not be documented through the incompatible sparse-checkout bootstrap"
fi

pass "Install commands and provenance guidance are consistent"
