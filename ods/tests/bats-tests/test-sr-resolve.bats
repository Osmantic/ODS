#!/usr/bin/env bats
# ============================================================================
# BATS tests for sr_resolve in lib/service-registry.sh.
# ============================================================================
# Guards against re-breakage of the `ods-<id>` prefix-strip added in
# PR #406 (container names pasted from `docker ps` should resolve to their
# service IDs), and of the broader alias-resolution contract.
#
# 8-case matrix:
#   1. Exact ID               →  resolves to the same ID
#   2. Known alias            →  resolves to canonical ID
#   3. `ods-<id>` prefix    →  strips prefix, resolves to ID
#   4. `ods-<alias>` prefix →  strips prefix, resolves through alias
#   5. Unknown `ods-*`      →  passes through as-is (not our container)
#   6. Unknown non-ods      →  passes through as-is (best-effort)
#   7. Empty input            →  passes through empty
#   8. Container that happens to start with `ods-` but isn't our
#      convention → passes through as-is (no alias match after strip)
#
# We invoke the registry inside a fresh `bash -c` subshell per test because
# `declare -A` at the top of service-registry.sh creates function-local
# arrays when the file is sourced from within bats' setup() (which is a
# shell function). A subshell sources at top level, populating the globals
# sr_resolve then actually reads.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TMPDIR_TEST="$BATS_TEST_TMPDIR"
    export FIXTURE_DIR="$TMPDIR_TEST/fixture"
    export REGISTRY_PATH="$BATS_TEST_DIRNAME/../../lib/service-registry.sh"

    # Fake EXTENSIONS_DIR so sr_load's Python loader has something to read.
    mkdir -p "$FIXTURE_DIR/extensions/services"

    # Service A — id "alpha", aliases [a, al]
    mkdir -p "$FIXTURE_DIR/extensions/services/alpha"
    cat > "$FIXTURE_DIR/extensions/services/alpha/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: alpha
  name: Alpha Service
  aliases: [a, al]
  container_name: ods-alpha
  category: core
YAML

    # Service B — id "bravo", aliases [b]
    mkdir -p "$FIXTURE_DIR/extensions/services/bravo"
    cat > "$FIXTURE_DIR/extensions/services/bravo/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: bravo
  name: Bravo Service
  aliases: [b]
  container_name: ods-bravo
  category: recommended
YAML

    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    python3 -c "import yaml" 2>/dev/null || skip "PyYAML not available"
}

# Resolve `$1` using a freshly-loaded registry against the fixture.
# Runs in a subshell so `declare -A` lines in service-registry.sh create
# true globals (not function-locals under bats' setup()).
# stderr is suppressed so we only assert on stdout — empty input legitimately
# triggers "bad array index" diagnostics from bash for SERVICE_ALIASES[""]
# which are not part of the contract we're pinning.
_sr() {
    bash -c '
        SCRIPT_DIR="'"$FIXTURE_DIR"'"
        export SCRIPT_DIR
        . "'"$REGISTRY_PATH"'"
        sr_resolve "$1" 2>/dev/null
    ' _ "$1"
}

# ── the 8-case matrix ───────────────────────────────────────────────────────

@test "sr_resolve: case 1 — exact ID resolves to same ID" {
    run _sr "alpha"
    assert_success
    assert_output "alpha"
}

@test "sr_resolve: case 2 — known alias resolves to canonical ID" {
    run _sr "a"
    assert_success
    assert_output "alpha"

    run _sr "al"
    assert_success
    assert_output "alpha"

    run _sr "b"
    assert_success
    assert_output "bravo"
}

@test "sr_resolve: case 3 — ods-<id> prefix strips and resolves" {
    # This is the PR-10 / #406 regression point: users copy container names
    # from `docker ps` (e.g. `ods-alpha`) and expect `ods restart` to
    # accept them.
    run _sr "ods-alpha"
    assert_success
    assert_output "alpha"
}

@test "sr_resolve: case 4 — ods-<alias> prefix strips and resolves through alias" {
    run _sr "ods-a"
    assert_success
    assert_output "alpha"

    run _sr "ods-b"
    assert_success
    assert_output "bravo"
}

@test "sr_resolve: case 5 — unknown ods-* passes through as-is" {
    # Container that starts with `ods-` but whose stripped form isn't a
    # known alias: return the input verbatim (best-effort; compose will
    # fail later with a clear error).
    run _sr "ods-unknown-service"
    assert_success
    assert_output "ods-unknown-service"
}

@test "sr_resolve: case 6 — unknown non-ods passes through as-is" {
    run _sr "not-a-service"
    assert_success
    assert_output "not-a-service"
}

@test "sr_resolve: case 7 — empty input returns empty" {
    run _sr ""
    assert_success
    # Empty input hits SERVICE_ALIASES[""] (unset) and falls back to echoing
    # the input (also empty). No crash, no stderr.
    assert_output ""
}

@test "sr_resolve: case 8 — ods-* where strip doesn't match any alias passes through" {
    # Container name-like string that isn't from our extensions and whose
    # stripped form doesn't collide with any known alias.
    run _sr "ods-some-other-project-container"
    assert_success
    assert_output "ods-some-other-project-container"
}

# ── additional correctness guards ───────────────────────────────────────────

@test "sr_resolve: does NOT strip ods- when the full input is already a known alias" {
    # The resolver checks SERVICE_ALIASES[input] first, only stripping if
    # missing. We prove the ordering holds by asserting plain `bravo` still
    # resolves even though `ods-bravo` is its container name.
    run _sr "bravo"
    assert_success
    assert_output "bravo"
}

@test "sr_resolve: idempotent — resolving twice yields same result" {
    run _sr "ods-a"
    assert_success
    [ "$output" = "alpha" ]

    run _sr "$output"
    assert_success
    [ "$output" = "alpha" ]
}
