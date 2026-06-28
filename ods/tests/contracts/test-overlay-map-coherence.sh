#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Contract test: OVERLAY_MAP ↔ hardware-classes.json coherence (issue #342)
#
# Two independent sources define compose overlay paths:
#   - scripts/classify-hardware.sh: OVERLAY_MAP dict + apple/macos special case
#   - config/hardware-classes.json: per-class recommended.compose_overlays
#
# This test asserts they agree for every hardware class.
#
# Run: bash tests/contracts/test-overlay-map-coherence.sh
# ============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

command -v python3 >/dev/null 2>&1 || { echo "[FAIL] python3 is required"; exit 1; }

echo "[contract] OVERLAY_MAP ↔ hardware-classes.json coherence"

python3 <<'PY'
import ast, json, re, sys

# --- Read OVERLAY_MAP from the embedded Python in classify-hardware.sh ---
with open("scripts/classify-hardware.sh", "r") as f:
    content = f.read()

m = re.search(r'OVERLAY_MAP\s*=\s*(\{[^}]+\})', content)
if not m:
    print("[FAIL] OVERLAY_MAP not found in scripts/classify-hardware.sh")
    sys.exit(1)

overlay_map = ast.literal_eval(m.group(1))

# Verify all four backends are present
for backend in ("amd", "nvidia", "apple", "cpu"):
    if backend not in overlay_map:
        print(f"[FAIL] OVERLAY_MAP missing backend: {backend}")
        sys.exit(1)

# --- Extract the apple+macos special case ---
m2 = re.search(
    r'if\s+backend\s*==\s*"apple"\s+and\s+platform_id\s*==\s*"macos":\s*\n'
    r'\s*overlays\s*=\s*(\[[^\]]+\])',
    content,
)
if not m2:
    print("[FAIL] apple+macos overlay override not found in scripts/classify-hardware.sh")
    sys.exit(1)

macos_overlays = ast.literal_eval(m2.group(1))

# --- Read hardware-classes.json ---
with open("config/hardware-classes.json", "r") as f:
    hw = json.load(f)

# --- Check every class ---
fail = 0
for cls in hw["classes"]:
    cid = cls["id"]
    backend = cls["recommended"]["backend"]
    actual = cls["recommended"]["compose_overlays"]
    platforms = cls.get("match", {}).get("platform_id", [])

    # Apple classes on macos use the special macos overlay
    if backend == "apple" and "macos" in platforms:
        expected = macos_overlays
        tag = f"{backend}+macos"
    else:
        if backend not in overlay_map:
            print(f"[FAIL] {cid}: backend '{backend}' not in OVERLAY_MAP")
            fail += 1
            continue
        expected = overlay_map[backend]
        tag = backend

    if actual != expected:
        print(f"[FAIL] {cid} ({tag}): expected {expected}, got {actual}")
        fail += 1
    else:
        print(f"  [PASS] {cid} ({tag})")

if fail > 0:
    sys.exit(1)
PY

echo "[PASS] OVERLAY_MAP ↔ hardware-classes.json coherence"
