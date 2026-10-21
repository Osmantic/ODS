#!/bin/bash
# ============================================================================
# Test: resolve-compose-stack.sh handles an install reached through a symlink
# ============================================================================
# An installed extension that builds locally gets a build-context overlay
# (data/user-extensions/<id>/.ods-build-context-compose.yaml.json) appended to
# the compose file list. The overlay path is derived from the resolved compose
# path, so when the install directory or its data/ directory is reached
# through a symlink (~/ods -> /mnt/disk/ods, data/ moved to another disk, or
# macOS /var -> /private/var) the resolver used to crash with
# "ValueError: ... is not in the subpath of ...". ods-cli then drops
# .compose-flags and every later `ods start` fails.
#
# Usage: bash tests/test-resolve-compose-symlinked-install.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASS=0
FAIL=0
pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    fail "PyYAML is required by resolve-compose-stack.sh (python3 -m pip install pyyaml)"
    exit 1
fi

SCRATCH="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$SCRATCH"' EXIT

OVERLAY="data/user-extensions/fixture-build/.ods-build-context-compose.yaml.json"

# Mirror the install tree (without data/) and install one locally-built
# extension into the given data directory.
make_install() {
    local install="$1" data="$2" entry name
    mkdir -p "$install"
    for entry in "$ROOT_DIR"/* "$ROOT_DIR"/.[!.]*; do
        [[ -e "$entry" ]] || continue
        name="$(basename "$entry")"
        [[ "$name" == data || "$name" == vendor ]] && continue
        ln -s "$entry" "$install/$name"
    done
    local ext="$data/user-extensions/fixture-build"
    mkdir -p "$ext"
    cat > "$ext/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: fixture-build
  name: Fixture Build
  type: docker
  port: 8080
  health: /health
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
YAML
    cat > "$ext/compose.yaml" <<'YAML'
services:
  fixture-build:
    build:
      context: .
      dockerfile: Dockerfile
    user: "1000:1000"
    security_opt:
      - no-new-privileges:true
    networks:
      - ods-network
networks:
  ods-network:
    external: true
    name: ods-network
YAML
    printf 'FROM scratch\n' > "$ext/Dockerfile"
}

# run_case <label> <script-dir>
run_case() {
    local label="$1" script_dir="$2" out="$SCRATCH/out" err="$SCRATCH/err" rc
    set +e
    bash "$RESOLVER" --script-dir "$script_dir" --tier 1 --gpu-backend cpu >"$out" 2>"$err"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        fail "$label: resolver exited $rc"
        sed 's/^/      /' "$err" | tail -n 5
        return
    fi
    if ! grep -qF -- "-f data/user-extensions/fixture-build/compose.yaml" "$out"; then
        fail "$label: installed extension missing from the stack"
        sed 's/^/      /' "$err"
        return
    fi
    if ! grep -qF -- "-f $OVERLAY" "$out"; then
        fail "$label: build-context overlay not emitted relative to the install"
        sed 's/^/      /' "$out"
        return
    fi
    if [[ ! -f "$script_dir/$OVERLAY" ]]; then
        fail "$label: emitted overlay does not exist under --script-dir"
        return
    fi
    pass "$label"
}

# Install directory reached through a symlink (e.g. ~/ods -> /mnt/disk/ods).
make_install "$SCRATCH/a/disk/ods" "$SCRATCH/a/disk/ods/data"
ln -s "$SCRATCH/a/disk/ods" "$SCRATCH/a/ods"
run_case "--script-dir is a symlink to the install" "$SCRATCH/a/ods"

# Physical install whose data/ directory lives on another disk.
make_install "$SCRATCH/b/ods" "$SCRATCH/b/disk/data"
ln -s "$SCRATCH/b/disk/data" "$SCRATCH/b/ods/data"
run_case "data/ is a symlink to another directory" "$SCRATCH/b/ods"

# Control: the plain physical layout keeps working.
make_install "$SCRATCH/c/ods" "$SCRATCH/c/ods/data"
run_case "physical install" "$SCRATCH/c/ods"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
