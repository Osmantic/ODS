#!/usr/bin/env bats
# ============================================================================
# BATS tests for scripts/ods-test-functional.sh set -e resilience.
# ============================================================================
# Guards against re-breakage of PR #428:
#   - Arithmetic expansion (TESTS_FAILED=$((TESTS_FAILED + 1))) must not
#     trip `set -e` on the first increment when the counter is 0.
#   - The summary line and the final exit code must be emitted even when
#     every underlying functional test fails.
#   - `set +e / -e` bounded around the test-function dispatch block lets
#     all tests run to completion before the summary.
#
# Note: the sentinel `__ODS_RESULT__` is emitted by the Python streaming
# endpoint (routers/setup.py), not by this shell script directly. The shell
# just needs to exit with the right code so the endpoint can report it.
# Sentinel-delivery itself is covered by PR-2F's Python tests.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TMPDIR_TEST="$BATS_TEST_TMPDIR"
    export SCRIPT_SRC="$BATS_TEST_DIRNAME/../../scripts/ods-test-functional.sh"
}

# Build a patched copy of the script with test_*_functional() overridden.
# Uses a single marker-insert to stick overrides just before the bounded
# `set +e` dispatch block, so the rest of the script (strict mode, counters,
# summary, exit logic) runs exactly as in production.
_patch_script() {
    local mode="$1"  # all-fail | all-pass | mixed
    local out="$TMPDIR_TEST/patched-${mode}.sh"
    local overrides_file="$TMPDIR_TEST/overrides-${mode}.sh"

    # Stub bodies for each mode. Every stub runs `pass` or `fail`, which
    # exist in the original script; those mutate the real counters.
    case "$mode" in
        all-fail)
            cat > "$overrides_file" <<'OV'
test_llm_functional()        { fail "LLM (stubbed)"; return 1; }
test_tts_functional()        { fail "TTS (stubbed)"; return 1; }
test_embeddings_functional() { fail "Embeddings (stubbed)"; return 1; }
test_whisper_functional()    { fail "Whisper (stubbed)"; return 1; }
OV
            ;;
        all-pass)
            cat > "$overrides_file" <<'OV'
test_llm_functional()        { pass "LLM (stubbed)"; }
test_tts_functional()        { pass "TTS (stubbed)"; }
test_embeddings_functional() { pass "Embeddings (stubbed)"; }
test_whisper_functional()    { pass "Whisper (stubbed)"; }
OV
            ;;
        mixed)
            cat > "$overrides_file" <<'OV'
test_llm_functional()        { pass "LLM (stubbed)"; }
test_tts_functional()        { fail "TTS (stubbed)"; return 1; }
test_embeddings_functional() { pass "Embeddings (stubbed)"; }
test_whisper_functional()    { fail "Whisper (stubbed)"; return 1; }
OV
            ;;
    esac

    # Insert overrides at the marker — the line immediately before the
    # bounded `set +e` dispatch block. BSD+GNU awk portable.
    awk -v ov_file="$overrides_file" '
        BEGIN {
            while ((getline line < ov_file) > 0) overrides = overrides line "\n"
            close(ov_file)
        }
        /^# Each test returns 1 on failure/ && !inserted {
            printf "%s", overrides
            inserted = 1
        }
        { print }
    ' "$SCRIPT_SRC" > "$out"

    # Neutralize the service-registry source block — it hard-depends on a
    # full install layout that does not exist in tmpdir. Strip surgically
    # by matching the opening `if [[ -f "$_FT_DIR/lib/service-registry.sh"`
    # to its closing `fi`. The `declare -A SERVICE_PORTS` line that follows
    # keeps the URL default-expansions safe.
    awk '
        /^if \[\[ -f "\$_FT_DIR\/lib\/service-registry\.sh" \]\]; then/ { in_block = 1; next }
        in_block && /^fi$/ { in_block = 0; next }
        !in_block { print }
    ' "$out" > "$out.tmp" && mv "$out.tmp" "$out"

    chmod +x "$out"
    echo "$out"
}

# ── all-fail path — the core regression (PR #428) ───────────────────────────

@test "resilience: summary line prints even when every test fails" {
    local script
    script=$(_patch_script all-fail)
    run bash "$script"
    # Script must exit 1 on any failure.
    [ "$status" -eq 1 ]
    # Summary must still appear.
    assert_output --partial "Results: 0 passed, 4 failed"
    assert_output --partial "Some functional tests failed"
}

@test "resilience: first fail call does not trip set -e at counter=0" {
    # The critical regression this guards against: `((TESTS_FAILED++))` under
    # set -e aborts the script on the FIRST call because the pre-increment
    # value is 0 and compound arithmetic returns that as exit code. With the
    # PR #428 fix (`TESTS_FAILED=$((TESTS_FAILED+1))`), the assignment form
    # always returns 0. If the first fail aborts the script, we'd see "0
    # passed, 1 failed" (only the first test ran). Assert we reached all 4.
    local script
    script=$(_patch_script all-fail)
    run bash "$script"
    assert_output --partial "4 failed"
}

# ── all-pass path ───────────────────────────────────────────────────────────

@test "resilience: all-pass exits 0 with full summary" {
    local script
    script=$(_patch_script all-pass)
    run bash "$script"
    assert_success
    assert_output --partial "Results: 4 passed, 0 failed"
    assert_output --partial "All functional tests passed"
}

# ── mixed path (regression guard for bounded set +e / -e) ───────────────────

@test "resilience: mixed pass/fail still runs every test and prints summary" {
    local script
    script=$(_patch_script mixed)
    run bash "$script"
    [ "$status" -eq 1 ]
    # All 4 test functions ran (2 pass, 2 fail).
    assert_output --partial "Results: 2 passed, 2 failed"
}

# ── static assertions on the resilience idioms in the script itself ─────────

@test "resilience: script uses arithmetic-expansion assignment (not ((++)))" {
    # TESTS_FAILED=$((TESTS_FAILED+1)) — the set-e-safe form.
    run grep -E 'TESTS_FAILED=\$\(\(TESTS_FAILED[[:space:]]*\+' "$SCRIPT_SRC"
    assert_success
    # And must NOT contain the dangerous ((TESTS_FAILED++)) form.
    run grep -E '\(\(TESTS_FAILED\+\+\)\)' "$SCRIPT_SRC"
    assert_failure
}

@test "resilience: script has bounded 'set +e' / 'set -e' around test dispatch" {
    run grep -n "^set +e" "$SCRIPT_SRC"
    assert_success
    run grep -n "^set -e" "$SCRIPT_SRC"
    assert_success
}

@test "resilience: TESTS_PASSED also uses the set-e-safe assignment form" {
    run grep -E 'TESTS_PASSED=\$\(\(TESTS_PASSED[[:space:]]*\+' "$SCRIPT_SRC"
    assert_success
}
