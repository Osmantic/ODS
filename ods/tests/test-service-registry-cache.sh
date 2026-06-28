#!/bin/bash
# Test suite for service registry caching
# Validates that compose flags are cached and cache invalidation works

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$SCRIPT_DIR/.."
SERVICE_REGISTRY="$ODS_DIR/lib/service-registry.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Test counters
PASSED=0
FAILED=0

# Test helpers
pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED++))
}

info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Test 1: Verify cache variables are initialized
test_cache_vars_initialized() {
    info "Test 1: Checking if cache variables are initialized"
    if grep -q "_SR_COMPOSE_FLAGS_CACHE=" "$SERVICE_REGISTRY" && \
       grep -q "_SR_COMPOSE_FLAGS_CACHED=false" "$SERVICE_REGISTRY"; then
        pass "Cache variables are initialized"
    else
        fail "Cache variables not properly initialized"
    fi
}

# Test 2: Verify cache statistics variables exist
test_cache_stats_vars() {
    info "Test 2: Checking if cache statistics variables exist"
    if grep -q "_SR_CACHE_HITS=0" "$SERVICE_REGISTRY" && \
       grep -q "_SR_CACHE_MISSES=0" "$SERVICE_REGISTRY"; then
        pass "Cache statistics variables exist"
    else
        fail "Cache statistics variables missing"
    fi
}

# Test 3: Verify sr_compose_flags checks cache
test_compose_flags_checks_cache() {
    info "Test 3: Checking if sr_compose_flags checks cache"
    if grep -A10 "^sr_compose_flags()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "_SR_COMPOSE_FLAGS_CACHED"; then
        pass "sr_compose_flags checks cache"
    else
        fail "sr_compose_flags does not check cache"
    fi
}

# Test 4: Verify cache hit increments counter
test_cache_hit_counter() {
    info "Test 4: Checking if cache hits increment counter"
    if grep -A15 "^sr_compose_flags()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "_SR_CACHE_HITS"; then
        pass "Cache hits increment counter"
    else
        fail "Cache hit counter not incremented"
    fi
}

# Test 5: Verify cache miss increments counter
test_cache_miss_counter() {
    info "Test 5: Checking if cache misses increment counter"
    if grep -A20 "^sr_compose_flags()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "_SR_CACHE_MISSES"; then
        pass "Cache misses increment counter"
    else
        fail "Cache miss counter not incremented"
    fi
}

# Test 6: Verify cache stores result
test_cache_stores_result() {
    info "Test 6: Checking if cache stores compose flags result"
    if grep -A30 "^sr_compose_flags()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "_SR_COMPOSE_FLAGS_CACHE="; then
        pass "Cache stores compose flags result"
    else
        fail "Cache does not store result"
    fi
}

# Test 7: Verify sr_cache_invalidate function exists
test_invalidate_function_exists() {
    info "Test 7: Checking if sr_cache_invalidate function exists"
    if grep -q "^sr_cache_invalidate()" "$SERVICE_REGISTRY" 2>/dev/null; then
        pass "sr_cache_invalidate function exists"
    else
        fail "sr_cache_invalidate function not found"
    fi
}

# Test 8: Verify invalidate clears cache flag
test_invalidate_clears_flag() {
    info "Test 8: Checking if invalidate clears cache flag"
    if grep -A5 "^sr_cache_invalidate()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "_SR_COMPOSE_FLAGS_CACHED=false"; then
        pass "Invalidate clears cache flag"
    else
        fail "Invalidate does not clear cache flag"
    fi
}

# Test 9: Verify invalidate clears cache value
test_invalidate_clears_value() {
    info "Test 9: Checking if invalidate clears cache value"
    if grep -A5 "^sr_cache_invalidate()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "_SR_COMPOSE_FLAGS_CACHE="; then
        pass "Invalidate clears cache value"
    else
        fail "Invalidate does not clear cache value"
    fi
}

# Test 10: Verify sr_cache_stats function exists
test_stats_function_exists() {
    info "Test 10: Checking if sr_cache_stats function exists"
    if grep -q "^sr_cache_stats()" "$SERVICE_REGISTRY" 2>/dev/null; then
        pass "sr_cache_stats function exists"
    else
        fail "sr_cache_stats function not found"
    fi
}

# Test 11: Verify stats shows cache hits
test_stats_shows_hits() {
    info "Test 11: Checking if stats displays cache hits"
    if grep -A10 "^sr_cache_stats()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "Cache Hits"; then
        pass "Stats displays cache hits"
    else
        fail "Stats does not display cache hits"
    fi
}

# Test 12: Verify stats shows cache misses
test_stats_shows_misses() {
    info "Test 12: Checking if stats displays cache misses"
    if grep -A10 "^sr_cache_stats()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "Cache Misses"; then
        pass "Stats displays cache misses"
    else
        fail "Stats does not display cache misses"
    fi
}

# Test 13: Verify stats calculates hit rate
test_stats_calculates_rate() {
    info "Test 13: Checking if stats calculates hit rate"
    if grep -A15 "^sr_cache_stats()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "Hit Rate"; then
        pass "Stats calculates hit rate"
    else
        fail "Stats does not calculate hit rate"
    fi
}

# Test 14: Verify sr_load invalidates cache on reload
test_load_invalidates_cache() {
    info "Test 14: Checking if sr_load invalidates cache"
    if grep -A10 "^sr_load()" "$SERVICE_REGISTRY" 2>/dev/null | \
       grep -q "sr_cache_invalidate\|_SR_COMPOSE_FLAGS_CACHED=false"; then
        pass "sr_load invalidates cache on reload"
    else
        fail "sr_load does not invalidate cache"
    fi
}

# Test 15: Verify service-registry.sh syntax is valid
test_syntax() {
    info "Test 15: Validating service-registry.sh syntax"
    if bash -n "$SERVICE_REGISTRY" 2>/dev/null; then
        pass "service-registry.sh syntax is valid"
    else
        fail "service-registry.sh has syntax errors"
    fi
}

# Run all tests
echo ""
echo -e "${BLUE}━━━ Service Registry Cache Tests ━━━${NC}"
echo ""

test_cache_vars_initialized
test_cache_stats_vars
test_compose_flags_checks_cache
test_cache_hit_counter
test_cache_miss_counter
test_cache_stores_result
test_invalidate_function_exists
test_invalidate_clears_flag
test_invalidate_clears_value
test_stats_function_exists
test_stats_shows_hits
test_stats_shows_misses
test_stats_calculates_rate
test_load_invalidates_cache
test_syntax

# Summary
echo ""
echo -e "${BLUE}━━━ Test Summary ━━━${NC}"
echo ""
echo -e "  ${GREEN}Passed:${NC} $PASSED"
if [[ $FAILED -gt 0 ]]; then
    echo -e "  ${RED}Failed:${NC} $FAILED"
fi
echo ""

if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
