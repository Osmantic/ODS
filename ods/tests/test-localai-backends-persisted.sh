#!/usr/bin/env bash
# LocalAI v3.x ships without inference backends; the first model install pulls
# its backend from the gallery into LOCALAI_BACKENDS_PATH (default /backends,
# declared as an image VOLUME). The library compose never bind-mounted that
# path, so installed backends lived in an anonymous volume that a
# `docker compose down` + up drops: models stay on disk under /models but fail
# to load until every backend is reinstalled. Assert the path is persisted.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${ODS_LOCALAI_COMPOSE_UNDER_TEST:-$ROOT_DIR/extensions/library/services/localai/compose.yaml}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$COMPOSE" ]] || fail "missing $COMPOSE"

grep -qE 'image:[[:space:]]*localai/localai:v3\.' "$COMPOSE" \
    || fail "expected a pinned LocalAI v3 image; re-verify the backends path for the new version"

backends_path="$(sed -nE 's/^[[:space:]]*-[[:space:]]*(LOCALAI_BACKENDS_PATH|BACKENDS_PATH)=(.*)$/\2/p' "$COMPOSE" | head -1)"
backends_path="${backends_path:-/backends}"

mount_line="$(grep -E "^[[:space:]]*-[[:space:]]*\./data/localai/[^:]+:${backends_path}(:rw)?[[:space:]]*\$" "$COMPOSE" || true)"
[[ -n "$mount_line" ]] \
    || fail "LocalAI backends path $backends_path is not bind-mounted under ./data/localai; installed backends are lost on recreate"
pass "LocalAI backends path $backends_path is persisted ($(echo "$mount_line" | sed 's/^ *- *//'))"
