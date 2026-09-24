#!/bin/bash
# ============================================================================
# Test: every community extension recipe survives installation
# ============================================================================
# The dashboard installs a library recipe by copying it into
# data/user-extensions/<id>/. From then on resolve-compose-stack.sh scans that
# compose on every `ods` invocation and silently drops it (a WARNING on stderr,
# exit 0) if it breaks an installed-extension rule: a build context outside
# the extension, extra_hosts, host networking, dangerous capabilities, ...
# A recipe like that installs cleanly and then never starts.
#
# Install every recipe into a scratch tree, run the real resolver for each
# GPU backend, and require every recipe to be part of the stack under at least
# one backend. GPU filtering legitimately excludes some recipes per backend; a
# recipe the resolver rejects is excluded under all of them.
#
# Usage: bash tests/test-library-recipes-resolve.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LIBRARY="$ROOT_DIR/extensions/library/services"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo -e "  ${RED}FAIL${NC} PyYAML is required by resolve-compose-stack.sh (python3 -m pip install pyyaml)"
    exit 1
fi

# Physical path: on macOS mktemp returns /var/..., a symlink to /private/var.
SCRATCH="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$SCRATCH"' EXIT

# Mirror the install tree without touching the real data/ directory. The
# resolver refuses symlinked extension directories, so recipes are copied.
for entry in "$ROOT_DIR"/* "$ROOT_DIR"/.[!.]*; do
    [[ -e "$entry" ]] || continue
    name="$(basename "$entry")"
    [[ "$name" == data || "$name" == vendor ]] && continue
    ln -s "$entry" "$SCRATCH/$name"
done
mkdir -p "$SCRATCH/data/user-extensions"
cp -R "$LIBRARY"/. "$SCRATCH/data/user-extensions/"

for backend in nvidia amd apple cpu; do
    bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
        --script-dir "$SCRATCH" --tier 1 --gpu-backend "$backend" \
        >"$SCRATCH/flags.$backend" 2>"$SCRATCH/warnings.$backend"
done

if ! python3 - "$LIBRARY" "$SCRATCH" <<'PY'
import pathlib
import sys

library = pathlib.Path(sys.argv[1])
scratch = pathlib.Path(sys.argv[2])
backends = ("nvidia", "amd", "apple", "cpu")

recipes = sorted(p.name for p in library.iterdir() if (p / "compose.yaml").is_file())
included = set()
for backend in backends:
    for token in (scratch / f"flags.{backend}").read_text().split():
        parts = token.split("/")
        if len(parts) == 4 and parts[:2] == ["data", "user-extensions"] and parts[3] == "compose.yaml":
            included.add(parts[2])

missing = [r for r in recipes if r not in included]
if not recipes:
    print("  no community extension recipes found")
    sys.exit(1)
if missing:
    warnings = set()
    for backend in backends:
        warnings.update((scratch / f"warnings.{backend}").read_text().splitlines())
    for recipe in missing:
        reasons = sorted(w for w in warnings if w.startswith(f"WARNING: {recipe}:"))
        print(f"  {recipe}: never included in the stack")
        for reason in reasons:
            print(f"      {reason}")
    sys.exit(1)
print(f"  {len(recipes)} recipes, all included under at least one GPU backend")
PY
then
    echo -e "  ${RED}FAIL${NC} installed community extensions are dropped by resolve-compose-stack.sh"
    exit 1
fi

echo -e "  ${GREEN}PASS${NC} every community extension recipe resolves once installed"
exit 0
