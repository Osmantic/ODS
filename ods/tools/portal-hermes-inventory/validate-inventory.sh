#!/usr/bin/env bash
# CI/release-gate hook for Portal-on-Hermes predecessor preservation inventory.
#
# Validates inventory integrity, runs the checker (with schema when available),
# and verifies the subsystem summary byte-for-byte against a fresh regeneration.
#
# Usage:
#   bash validate-inventory.sh [inventory-dir]
#
# Exit 0 = gate passed, exit 1 = gate failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INVENTORY_DIR="${1:-$SCRIPT_DIR}"
PYTHON_CMD="${PYTHON_CMD:-python3}"
INVENTORY_JSON="${INVENTORY_DIR}/inventory.json"
CHECKER="${INVENTORY_DIR}/check_inventory.py"
SCHEMA="${INVENTORY_DIR}/inventory.schema.json"
SUMMARIZER="${INVENTORY_DIR}/generate_summary.py"
SUMMARY="${INVENTORY_DIR}/subsystem_summary.md"

# ── Pre-flight ───────────────────────────────────────────────────────

if [ ! -f "$INVENTORY_JSON" ]; then
    echo "GATE FAIL: Inventory file not found: $INVENTORY_JSON"
    exit 1
fi

if [ ! -f "$CHECKER" ]; then
    echo "GATE FAIL: Checker not found: $CHECKER"
    exit 1
fi

if [ ! -f "$SUMMARIZER" ]; then
    echo "GATE FAIL: Summarizer not found: $SUMMARIZER"
    exit 1
fi

# ── Run checker ──────────────────────────────────────────────────────

echo "=== Portal-on-Hermes Inventory Gate ==="
echo ""

CHECK_CMD=("$PYTHON_CMD" "$CHECKER" "$INVENTORY_JSON")

if [ ! -f "$SCHEMA" ]; then
    echo "GATE FAIL: Schema not found: $SCHEMA"
    exit 1
fi

CHECK_CMD+=(--schema "$SCHEMA")
if "$PYTHON_CMD" -c "import jsonschema" 2>/dev/null; then
    echo "[info] jsonschema available — draft-07 schema validation enabled"
else
    echo "[fail] jsonschema is required when schema validation is requested"
fi

if ! "${CHECK_CMD[@]}"; then
    echo ""
    echo "GATE FAIL: Inventory validation failed."
    exit 1
fi

echo ""

# ── Verify subsystem summary byte-for-byte ──────────────────────────

if [ ! -f "$SUMMARY" ]; then
    echo "GATE FAIL: Checked-in summary not found: $SUMMARY"
    exit 1
fi

# Generate a fresh summary to a temporary file
TMP_SUMMARY="$(mktemp)"
trap 'rm -f "$TMP_SUMMARY"' EXIT

if ! "$PYTHON_CMD" "$SUMMARIZER" "$INVENTORY_JSON" --out "$TMP_SUMMARY"; then
    echo "GATE FAIL: Summarizer execution failed."
    exit 1
fi

# Byte-for-byte comparison (no silent rewrite)
if ! cmp -s "$SUMMARY" "$TMP_SUMMARY"; then
    echo "GATE FAIL: Subsystem summary mismatch — checked-in summary differs from fresh regeneration."
    echo "  Checked-in: $SUMMARY"
    echo "  Fresh:      $TMP_SUMMARY"
    diff -u "$SUMMARY" "$TMP_SUMMARY" || true
    exit 1
fi

echo "=== Subsystem summary verified (byte-for-byte match) ==="

echo ""
echo "=== Portal-on-Hermes Inventory Gate: PASSED ==="
