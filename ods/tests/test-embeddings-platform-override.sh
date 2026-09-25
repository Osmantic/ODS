#!/bin/bash
# Regression (#2302): embeddings compose hardcoded platform: linux/amd64.
# TEI has no arm64 image; the comment cited Rosetta 2 (macOS), but native
# arm64 Linux hosts (Raspberry Pi 5, Graviton, aarch64 Colima, most ARM
# SBCs) have no Rosetta and would run the container under slow QEMU
# emulation or fail to start. EMBEDDINGS_PLATFORM makes it configurable
# while keeping the same default.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "  (skipped — docker compose not available)"
    exit 0
fi

COMPOSE_FILE="extensions/services/embeddings/compose.yaml"

echo "── embeddings platform override ──"

rendered_default="$(unset EMBEDDINGS_PLATFORM 2>/dev/null; docker compose -f "$COMPOSE_FILE" config 2>/dev/null | grep 'platform:')"
if [[ "$rendered_default" == *"linux/amd64"* ]]; then
    pass "default platform is unchanged (linux/amd64)"
else
    fail "default platform changed unexpectedly (got: $rendered_default)"
fi

rendered_override="$(EMBEDDINGS_PLATFORM=linux/arm64 docker compose -f "$COMPOSE_FILE" config 2>/dev/null | grep 'platform:')"
if [[ "$rendered_override" == *"linux/arm64"* ]]; then
    pass "EMBEDDINGS_PLATFORM overrides the default"
else
    fail "EMBEDDINGS_PLATFORM override was not applied (got: $rendered_override)"
fi

# validate-env.sh must accept the new key, not flag it as unknown.
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT
echo "EMBEDDINGS_PLATFORM=linux/arm64" > "$TMPDIR_TEST/test.env"
validate_out="$(bash scripts/validate-env.sh "$TMPDIR_TEST/test.env" .env.schema.json 2>&1 || true)"
if echo "$validate_out" | grep -qi "EMBEDDINGS_PLATFORM"; then
    fail "validate-env.sh flags EMBEDDINGS_PLATFORM as unknown/invalid: $validate_out"
else
    pass "EMBEDDINGS_PLATFORM is a recognized schema key"
fi

echo ""
echo "  All checks passed"
