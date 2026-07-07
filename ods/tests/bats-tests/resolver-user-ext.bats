#!/usr/bin/env bats
# ============================================================================
# BATS tests for scripts/resolve-compose-stack.sh — USER-EXTENSION loop.
# ============================================================================
# Issues #489 + #508. The user-installed extension loop must respect the same
# `gpu_backends` filter as the built-in loop (#489) and must apply the same
# mode/GPU overlay-discovery rules (#508): GPU overlay, local-mode overlay,
# multi-GPU overlay — with the apple-deadlock guard skipping `compose.local.yaml`
# whenever gpu_backend == "apple".
#
# DRAFT NOTE — the user-extension loop on `upstream/main` (commit d19a17ff)
# pre-dates these features and ships the minimal carve-out that just appends
# `compose.yaml` + `compose.<gpu>.yaml` if present. Several tests below will
# therefore FAIL on the current tree and only pass once PR #1051
# (fix/resolver-python-hygiene) merges. See per-test inline comments for the
# expected gating.
#
# Strategy: build a minimal `script_dir` fixture inside BATS_TEST_TMPDIR with
# only the files the resolver actually reads (docker-compose.base.yml plus
# the GPU overlays + the user-extensions tree). Invoke
# `scripts/resolve-compose-stack.sh` with `--script-dir` pointing at the
# fixture and assert on stdout (the merged `-f file -f file ...` flag list).

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../../scripts/resolve-compose-stack.sh"
    export SCRIPT_UNDER_TEST

    export FIXTURE_DIR="$BATS_TEST_TMPDIR/fixture"
    mkdir -p "$FIXTURE_DIR"

    # Minimal compose-file presence so the resolver picks a base+overlay pair
    # for any of the GPU backends used by the tests below.
    : > "$FIXTURE_DIR/docker-compose.base.yml"
    : > "$FIXTURE_DIR/docker-compose.nvidia.yml"
    : > "$FIXTURE_DIR/docker-compose.amd.yml"
    : > "$FIXTURE_DIR/docker-compose.apple.yml"
    : > "$FIXTURE_DIR/docker-compose.multigpu.yml"

    mkdir -p "$FIXTURE_DIR/data/user-extensions"
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/fixture"
}

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# Write a manifest with optional gpu_backends and compose_file fields.
# Args: <ext_name> <key=value>...    where keys are from the manifest's
# `service:` map (gpu_backends as JSON array or string, compose_file).
_write_user_ext_manifest() {
    local ext="$1"; shift
    local ext_dir="$FIXTURE_DIR/data/user-extensions/$ext"
    mkdir -p "$ext_dir"
    local svc_block=""
    for kv in "$@"; do
        local key="${kv%%=*}"
        local val="${kv#*=}"
        svc_block+="  ${key}: ${val}"$'\n'
    done
    cat > "$ext_dir/manifest.yaml" <<MANIFEST
schema_version: ods.services.v1
id: ${ext}
service:
${svc_block}
MANIFEST
    # Always create a placeholder compose.yaml so the GPU/mode overlay
    # discovery has something to attach to (the resolver only walks
    # overlays when the base compose exists). The resolver's user-ext
    # content scanner requires a YAML mapping at the top level, so write
    # a minimal `services: {}` block rather than an empty file.
    printf 'services: {}\n' > "$ext_dir/compose.yaml"
}

_write_user_ext_overlay() {
    local ext="$1"
    local overlay_name="$2"
    # Same YAML-mapping requirement as the base compose above.
    printf 'services: {}\n' > "$FIXTURE_DIR/data/user-extensions/$ext/$overlay_name"
}

# Run the resolver against the fixture. Args are extra flags
# (e.g. --gpu-backend amd --tier 1).
_run_resolver() {
    run env ODS_MODE="${ODS_MODE:-local}" \
        bash "$SCRIPT_UNDER_TEST" --script-dir "$FIXTURE_DIR" "$@"
}

_assert_output_path() {
    local needle="$1"
    local normalized="${output//\\//}"
    if [[ "$normalized" != *"$needle"* ]]; then
        echo "expected output to contain path: $needle" >&2
        echo "$output" >&2
        return 1
    fi
}

_refute_output_path() {
    local needle="$1"
    local normalized="${output//\\//}"
    if [[ "$normalized" == *"$needle"* ]]; then
        echo "expected output not to contain path: $needle" >&2
        echo "$output" >&2
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Issue #489 — gpu_backends filter on user extensions.
# ---------------------------------------------------------------------------

@test "user-ext (DRAFT, needs #1051): amd-only manifest is excluded on nvidia backend" {
    _write_user_ext_manifest "amdsvc" "gpu_backends=[amd]"

    _run_resolver --gpu-backend nvidia --tier 1
    assert_success
    _refute_output_path "data/user-extensions/amdsvc/compose.yaml"
}

@test "user-ext (DRAFT, needs #1051): nvidia-only manifest is included on nvidia backend" {
    _write_user_ext_manifest "nvsvc" "gpu_backends=[nvidia]"

    _run_resolver --gpu-backend nvidia --tier 1
    assert_success
    _assert_output_path "data/user-extensions/nvsvc/compose.yaml"
}

@test "user-ext (DRAFT, needs #1051): 'all' in gpu_backends is included on every backend" {
    _write_user_ext_manifest "anysvc" "gpu_backends=[all]"

    _run_resolver --gpu-backend amd --tier 1
    assert_success
    _assert_output_path "data/user-extensions/anysvc/compose.yaml"
}

@test "user-ext (DRAFT, needs #1051): 'none' in gpu_backends is included on every backend (CPU-only services)" {
    _write_user_ext_manifest "cpusvc" "gpu_backends=[none]"

    _run_resolver --gpu-backend amd --tier 1
    assert_success
    _assert_output_path "data/user-extensions/cpusvc/compose.yaml"
}

@test "user-ext (DRAFT, needs #1051): empty gpu_backends excludes the extension on every backend" {
    # The PR #1051 predicate is `gpu_backend not in backends and "all" not in
    # backends and "none" not in backends`. With backends=[] all three
    # conditions are true, so the extension is filtered out. This is the
    # documented behavior of #1051 — if a future PR widens it to "empty
    # list = include everywhere" this test must be updated.
    _write_user_ext_manifest "emptysvc" "gpu_backends=[]"

    _run_resolver --gpu-backend nvidia --tier 1
    assert_success
    _refute_output_path "data/user-extensions/emptysvc/compose.yaml"
}

@test "user-ext: legacy extension with no manifest at all is still included (compat carve-out)" {
    # Pre-#1051: included unconditionally.
    # Post-#1051: included via the manifest-less compat branch
    # (`isinstance(manifest, dict)` gate skips the gpu_backends filter).
    # Either way the extension's compose.yaml must appear in the resolved set.
    local ext_dir="$FIXTURE_DIR/data/user-extensions/legacysvc"
    mkdir -p "$ext_dir"
    printf 'services: {}\n' > "$ext_dir/compose.yaml"

    _run_resolver --gpu-backend nvidia --tier 1
    assert_success
    _assert_output_path "data/user-extensions/legacysvc/compose.yaml"
}

# ---------------------------------------------------------------------------
# Issue #508 — overlay discovery (mode + multigpu + apple-deadlock guard).
# ---------------------------------------------------------------------------

@test "user-ext (DRAFT, needs #1051): compose.local.yaml is included in local mode on nvidia" {
    _write_user_ext_manifest "localsvc" "gpu_backends=[nvidia]"
    _write_user_ext_overlay "localsvc" "compose.local.yaml"

    ODS_MODE=local _run_resolver --gpu-backend nvidia --tier 1
    assert_success
    _assert_output_path "data/user-extensions/localsvc/compose.yaml"
    _assert_output_path "data/user-extensions/localsvc/compose.local.yaml"
}

@test "user-ext (DRAFT, needs #1051): compose.local.yaml is excluded on apple backend (deadlock guard)" {
    # Apple Silicon runs llama-server natively (replicas: 0 in compose), so
    # `depends_on: llama-server: service_healthy` inside compose.local.yaml
    # never satisfies and deadlocks. PR #1051 keeps the same guard the
    # built-in loop already has (PR #1004).
    # NOTE: passes today vacuously — the resolver doesn't yet discover
    # compose.local.yaml on the user-ext loop at all; this test only becomes
    # a real regression shield once #1051 lands the discovery + apple guard
    # together. Pre-#1051 the apple guard could be missing entirely and
    # this test would still pass, because the resolver never discovers
    # the overlay on the user-ext loop in the first place.
    _write_user_ext_manifest "applelocalsvc" "gpu_backends=[apple,nvidia,amd]"
    _write_user_ext_overlay "applelocalsvc" "compose.local.yaml"

    ODS_MODE=local _run_resolver --gpu-backend apple --tier AP_BASE
    assert_success
    _assert_output_path "data/user-extensions/applelocalsvc/compose.yaml"
    _refute_output_path "data/user-extensions/applelocalsvc/compose.local.yaml"
}

@test "user-ext (DRAFT, needs #1051): compose.multigpu.yaml is included when --gpu-count > 1" {
    _write_user_ext_manifest "multisvc" "gpu_backends=[nvidia]"
    _write_user_ext_overlay "multisvc" "compose.multigpu.yaml"

    _run_resolver --gpu-backend nvidia --tier 1 --gpu-count 2
    assert_success
    _assert_output_path "data/user-extensions/multisvc/compose.yaml"
    _assert_output_path "data/user-extensions/multisvc/compose.multigpu.yaml"
}

@test "user-ext (DRAFT, needs #1051): compose.multigpu.yaml is NOT included when --gpu-count == 1" {
    _write_user_ext_manifest "multisvc" "gpu_backends=[nvidia]"
    _write_user_ext_overlay "multisvc" "compose.multigpu.yaml"

    _run_resolver --gpu-backend nvidia --tier 1 --gpu-count 1
    assert_success
    _assert_output_path "data/user-extensions/multisvc/compose.yaml"
    _refute_output_path "data/user-extensions/multisvc/compose.multigpu.yaml"
}

@test "user-ext: compose.<gpu>.yaml overlay is included when present (already on main)" {
    # GPU-backend-specific overlay discovery already exists pre-#1051 — this
    # test guards against a regression that drops it during the refactor.
    local ext_dir="$FIXTURE_DIR/data/user-extensions/gpusvc"
    mkdir -p "$ext_dir"
    printf 'services: {}\n' > "$ext_dir/compose.yaml"
    printf 'services: {}\n' > "$ext_dir/compose.nvidia.yaml"

    _run_resolver --gpu-backend nvidia --tier 1
    assert_success
    _assert_output_path "data/user-extensions/gpusvc/compose.yaml"
    _assert_output_path "data/user-extensions/gpusvc/compose.nvidia.yaml"
}
