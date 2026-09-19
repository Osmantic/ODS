#!/usr/bin/env bash
# Contract: every bundled ComfyUI custom-node layer must fail the image build
# when its dependency install fails. A layer that ends `pip3 install ... ||
# true` produces a green image whose node is present but broken at runtime
# (issue #5664).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

DOCKERFILE="extensions/services/comfyui/Dockerfile"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$DOCKERFILE" ]] || fail "missing contract input: $DOCKERFILE"

# Static contract: no pip install line may suppress its own failure.
if grep -nE 'pip3? install.*(\|\|[[:space:]]*true|2>/dev/null)' "$DOCKERFILE"; then
    fail "ComfyUI Dockerfile suppresses a pip install failure"
fi
pass "no pip install line suppresses its exit status"

# Behavioral check: replay each custom-node RUN chain with a failing pip3 and
# require the chain to exit non-zero (as a real docker build would).
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

export COMFYUI_DIR="$tmp_dir/comfyui"
git() { return 0; }
pip3() { return 1; }

for node in ComfyUI-Manager ComfyUI-GGUF ComfyUI-KJNodes ComfyUI-VideoHelperSuite ComfyUI-LTXVideo; do
    mkdir -p "$COMFYUI_DIR/custom_nodes/$node"
    # Extract the node's RUN chain: capture the custom_nodes RUN block that
    # names the node (the name appears on the git clone line, not the RUN line).
    block="$(awk -v node="$node" '
        /^RUN .*custom_nodes/ { capture = 1; buf = "" }
        capture { buf = buf $0 "\n"; if ($0 !~ /\\$/) { if (buf ~ node) printf "%s", buf; capture = 0 } }
    ' "$DOCKERFILE")"
    [[ -n "$block" ]] || fail "no RUN layer found for $node"
    cmd="$(printf '%s' "$block" | sed 's/^RUN //; s/[[:space:]]*\\$//' | tr '\n' ' ')"
    if ( eval "$cmd" ) 2>/dev/null; then
        fail "$node layer swallows a pip install failure"
    fi
done
pass "all 5 custom-node layers propagate a failing dependency install"
