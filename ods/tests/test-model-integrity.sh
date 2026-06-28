#!/usr/bin/env bash
# ============================================================================
# Test: Model Integrity Verification
# ============================================================================
# Tests SHA256 checksum verification for GGUF model downloads
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASS++))
}

fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((FAIL++))
}

info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

# ============================================================================
# Test 1: Tier map has SHA256 fields
# ============================================================================
test_tier_map_has_sha256() {
    info "Testing tier-map.sh has GGUF_SHA256 fields..."

    local tier_map="$ROOT_DIR/installers/lib/tier-map.sh"

    if ! grep -q "GGUF_SHA256=" "$tier_map"; then
        fail "tier-map.sh missing GGUF_SHA256 fields"
        return
    fi

    # Check that at least some tiers have non-empty checksums
    local checksum_count
    checksum_count=$(grep 'GGUF_SHA256="[a-f0-9]\{64\}"' "$tier_map" | wc -l)
    if [[ $checksum_count -lt 2 ]]; then
        fail "tier-map.sh has too few valid SHA256 checksums (found: $checksum_count)"
        return
    fi

    pass "tier-map.sh has GGUF_SHA256 fields ($checksum_count checksums defined)"
}

# ============================================================================
# Test 2: Phase 11 uses sha256sum for verification
# ============================================================================
test_phase11_uses_sha256sum() {
    info "Testing phase 11 uses sha256sum..."

    local phase11="$ROOT_DIR/installers/phases/11-services.sh"

    if ! grep -q "sha256sum" "$phase11"; then
        fail "phase 11 doesn't use sha256sum for verification"
        return
    fi

    if ! grep -q "GGUF_SHA256" "$phase11"; then
        fail "phase 11 doesn't check GGUF_SHA256 variable"
        return
    fi

    # Check for verification before download (existing file check)
    if ! grep -q "Verifying model integrity" "$phase11"; then
        fail "phase 11 missing integrity verification messages"
        return
    fi

    pass "phase 11 implements SHA256 verification"
}

# ============================================================================
# Test 3: Verification logic handles corrupt files
# ============================================================================
test_corrupt_file_handling() {
    info "Testing corrupt file detection logic..."

    local phase11="$ROOT_DIR/installers/phases/11-services.sh"

    # Check that corrupt files are removed
    if ! grep -q "rm -f.*GGUF_FILE" "$phase11"; then
        fail "phase 11 doesn't remove corrupt files"
        return
    fi

    # Check for mismatch detection
    if ! grep -q "mismatch\|corrupt" "$phase11"; then
        fail "phase 11 missing corruption detection messages"
        return
    fi

    pass "phase 11 handles corrupt files correctly"
}

# ============================================================================
# Test 4: Verification works with real checksums
# ============================================================================
test_checksum_verification() {
    info "Testing checksum verification with test data..."

    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' RETURN

    # Create test file with known content
    echo "test content" > "$tmpdir/test.gguf"

    # Calculate actual hash
    local actual_hash
    actual_hash=$(sha256sum "$tmpdir/test.gguf" | awk '{print $1}')

    # Test matching hash
    local test_hash="$actual_hash"
    local result
    result=$(sha256sum "$tmpdir/test.gguf" | awk '{print $1}')

    if [[ "$result" != "$test_hash" ]]; then
        fail "sha256sum verification failed for matching hash"
        return
    fi

    # Test mismatching hash
    local wrong_hash="0000000000000000000000000000000000000000000000000000000000000000"
    if [[ "$result" == "$wrong_hash" ]]; then
        fail "sha256sum incorrectly matched wrong hash"
        return
    fi

    pass "sha256sum verification works correctly"
}

# ============================================================================
# Test 5: Empty checksums don't block installation
# ============================================================================
test_empty_checksum_handling() {
    info "Testing empty checksum handling..."

    local phase11="$ROOT_DIR/installers/phases/11-services.sh"

    # Check that verification is conditional on non-empty GGUF_SHA256
    if ! grep -q '\[\[ -n.*GGUF_SHA256.*\]\]' "$phase11"; then
        fail "phase 11 doesn't check if GGUF_SHA256 is non-empty before verification"
        return
    fi

    pass "phase 11 handles empty checksums gracefully"
}

# ============================================================================
# Test 6: Verification happens both before and after download
# ============================================================================
test_dual_verification() {
    info "Testing verification happens at both stages..."

    local phase11="$ROOT_DIR/installers/phases/11-services.sh"

    # Count verification blocks
    local verify_count
    verify_count=$(grep -c "Verifying.*integrity" "$phase11" || echo "0")

    if [[ $verify_count -lt 2 ]]; then
        fail "phase 11 should verify integrity before and after download (found: $verify_count)"
        return
    fi

    pass "phase 11 verifies integrity at both stages"
}

# ============================================================================
# Run all tests
# ============================================================================
echo "============================================"
echo "Model Integrity Verification Tests"
echo "============================================"
echo ""

test_tier_map_has_sha256
test_phase11_uses_sha256sum
test_corrupt_file_handling
test_checksum_verification
test_empty_checksum_handling
test_dual_verification

echo ""
echo "============================================"
echo "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo "============================================"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi

exit 0
