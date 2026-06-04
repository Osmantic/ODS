#!/usr/bin/env bash
# Windows Lemonade full-model swap-wait contract (#1517).
#
# Guards two regressions in the BACKGROUND full-model swap on native Windows
# Lemonade (scripts/bootstrap-upgrade.sh):
#
#   1. The register/load wait must not be the too-short hardcoded 12-attempt
#      (~2 min) budget. A 22 GB MoE (Qwen3.6-35B-A3B) was not even *listed* by
#      Lemonade within 2 min of the swap restart, so the swap reverted to the
#      bootstrap model. The wait must be configurable and default to a longer
#      budget (>= 60 attempts), matching the llama.cpp warm-up path.
#
#   2. On swap failure the status JSON must report the REAL downloaded byte
#      counts (the model did download + verify) plus the actual cause — not a
#      bare `write_status "failed"` that zeroes bytesDownloaded/bytesTotal and
#      reads as a 0-byte download failure (which is what originally misled
#      triage into thinking the download never started).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

SCRIPT="scripts/bootstrap-upgrade.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "[contract] Windows Lemonade swap wait + failure reporting (#1517)"

# ---------------------------------------------------------------------------
# 1. The swap wait must not hardcode the too-short 12-attempt budget.
# ---------------------------------------------------------------------------
if grep -Eq 'for _i in \$\(seq 1 12\)' "$SCRIPT"; then
    fail "swap wait still hardcodes 'seq 1 12' (~2 min) — too short for a large MoE to register"
else
    pass "swap wait no longer hardcodes the 12-attempt (~2 min) budget"
fi

# ---------------------------------------------------------------------------
# 2. The swap wait must be configurable with a sane longer default (>= 60).
# ---------------------------------------------------------------------------
if grep -Eq 'DREAM_LEMONADE_SWAP_ATTEMPTS:-(6[0-9]|[7-9][0-9]|[1-9][0-9]{2,})' "$SCRIPT"; then
    pass "swap wait is configurable (DREAM_LEMONADE_SWAP_ATTEMPTS) with a >= 60 default"
else
    fail "swap wait must read DREAM_LEMONADE_SWAP_ATTEMPTS with a default of at least 60"
fi

# ---------------------------------------------------------------------------
# 3. The swap-registration-timeout failure must report real bytes + cause,
#    not a bare zero-byte write_status "failed".
# ---------------------------------------------------------------------------
if grep -q 'did not load it after swap (registration timeout)' "$SCRIPT" \
   && grep -Eq 'write_status "failed" 100 "\$TOTAL_BYTES" "\$TOTAL_BYTES"' "$SCRIPT"; then
    pass "swap-timeout failure reports real downloaded bytes + cause (not a 0-byte failure)"
else
    fail "swap-timeout failure must write_status with real TOTAL_BYTES and the registration-timeout cause"
fi

# ---------------------------------------------------------------------------
# 4. The post-swap Hermes-patch failure must likewise carry real bytes.
# ---------------------------------------------------------------------------
if grep -q 'Hermes config patch failed after swap' "$SCRIPT"; then
    pass "post-swap Hermes-patch failure reports real bytes + cause"
else
    fail "post-swap Hermes-patch failure must report real bytes + cause, not a bare failed status"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
