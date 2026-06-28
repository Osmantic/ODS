#!/usr/bin/env bats
# ============================================================================
# BATS tests for ods-cli's shell-flag hygiene.
# ============================================================================
# Guards against re-breakage of:
#   - PR #410 / nounset audit:     line 6 must be `set -euo pipefail`
#   - Pipefail SIGPIPE audit:      `sed -n '1p'` must be used (not `head -1`)
#   - Nounset audit:               minimal-env invocations must not crash
#                                  on undefined variables
#
# These are static-assertion tests (grep, sed) plus one subprocess invocation
# under `env -i` to catch any bare `${FOO}` that sneaks back in.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export CLI="$BATS_TEST_DIRNAME/../../ods-cli"
}

# ── line 6 shell-mode assertion (PR #410) ───────────────────────────────────

@test "flags: line 6 is exactly 'set -euo pipefail'" {
    run sed -n '6p' "$CLI"
    assert_success
    assert_output "set -euo pipefail"
}

@test "flags: no weaker shell-mode line exists anywhere in ods-cli" {
    # If someone accidentally re-introduces `set -eo pipefail` (dropping
    # nounset) or bare `set -e`, this catches it.
    run grep -nE '^set -e[^u]' "$CLI"
    # grep returning 1 means no match — that's what we want.
    assert_failure
}

# ── sed -n '1p' replacement for `head -1` (pipefail SIGPIPE audit) ──────────

@test "flags: no bare '| head -1' pipelines in ods-cli" {
    # `| head -1` under `set -o pipefail` can SIGPIPE the upstream command
    # and abort. Project-blessed replacement is `| sed -n '1p'`.
    run grep -nE '\| head -1([^0-9]|$)' "$CLI"
    assert_failure
}

@test "flags: sed -n '1p' idiom is present where bootstrap + preset code path needs it" {
    # Just assert the idiom is used — the exact count can drift with
    # refactors, but it must be >0 to prove the replacement happened.
    run grep -cE "sed -n '1p'" "$CLI"
    assert_success
    [ "$output" -gt 0 ]
}

# ── nounset syntax: conditional var references use ${FOO:-default} ──────────

@test "flags: conditional var references follow \${FOO:-default} form" {
    # The nounset audit (PR #11) converted many bare `${VAR}` refs that
    # fire only on optional/env-dependent paths to `${VAR:-}` / `${VAR:-default}`.
    # Assert that the characteristic pattern is widely present; >20 occurrences
    # confirm the audit changes are still in place.
    run grep -cE '\$\{[A-Za-z_][A-Za-z_0-9]*:-' "$CLI"
    assert_success
    [ "$output" -gt 20 ]
}

# ── end-to-end: ods-cli doesn't crash on nounset with minimal env ─────────

@test "flags: --version runs under minimal env without unbound-var crash" {
    # Strip the environment down to the bare minimum. If ods-cli references
    # any var without `:-` on the --version / --help code path, `set -u`
    # aborts here and this test fails.
    run env -i \
        HOME="/tmp" \
        PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/Volumes/X/homebrew/bin" \
        TERM="dumb" \
        bash "$CLI" --version
    # --version exits 0 or reports a help summary; either way it must not
    # crash with "unbound variable".
    refute_output --partial "unbound variable"
}

@test "flags: help runs under minimal env without unbound-var crash" {
    run env -i \
        HOME="/tmp" \
        PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/Volumes/X/homebrew/bin" \
        TERM="dumb" \
        bash "$CLI" help
    refute_output --partial "unbound variable"
}

# ── script parses under bash -n ─────────────────────────────────────────────

@test "flags: ods-cli passes bash -n (syntax check)" {
    run bash -n "$CLI"
    assert_success
}
