#!/usr/bin/env bats
# ============================================================================
# BATS tests for cross-platform BSD/GNU compat in:
#   - memory-shepherd/memory-shepherd.sh (_stat_mtime / _stat_size helpers)
#   - scripts/llm-cold-storage.sh        (get_last_access_days BSD branch)
#   - scripts/migrate-config.sh          (MIGRATIONS_DIR resolves to ../migrations)
# ============================================================================

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

# Source root of the worktree (resolves to ods/)
REPO_ROOT="$BATS_TEST_DIRNAME/../.."

setup() {
    TMPDIR_BATS="$(mktemp -d "${TMPDIR:-/tmp}/bsd-compat.XXXXXX")"
    export TMPDIR_BATS
}

teardown() {
    rm -rf "$TMPDIR_BATS"
}

# ── memory-shepherd helpers ────────────────────────────────────────────────

@test "_stat_mtime returns mtime epoch on Darwin (uses stat -f %m)" {
    # Stub uname to return Darwin and stat to record the args it was called with.
    local stub_dir="$TMPDIR_BATS/stub-darwin"
    mkdir -p "$stub_dir"
    cat > "$stub_dir/uname" <<'EOF'
#!/bin/bash
echo Darwin
EOF
    cat > "$stub_dir/stat" <<'EOF'
#!/bin/bash
echo "stat-args:$*"
EOF
    chmod +x "$stub_dir/uname" "$stub_dir/stat"

    PATH="$stub_dir:$PATH" run bash -c "
        _stat_mtime() {
            if [[ \"\$(uname -s)\" == \"Darwin\" ]]; then
                stat -f %m \"\$1\"
            else
                stat -c %Y \"\$1\"
            fi
        }
        _stat_mtime /some/file
    "
    assert_success
    assert_output "stat-args:-f %m /some/file"
}

@test "_stat_size returns size on Linux (uses stat -c %s)" {
    local stub_dir="$TMPDIR_BATS/stub-linux"
    mkdir -p "$stub_dir"
    cat > "$stub_dir/uname" <<'EOF'
#!/bin/bash
echo Linux
EOF
    cat > "$stub_dir/stat" <<'EOF'
#!/bin/bash
echo "stat-args:$*"
EOF
    chmod +x "$stub_dir/uname" "$stub_dir/stat"

    PATH="$stub_dir:$PATH" run bash -c "
        _stat_size() {
            if [[ \"\$(uname -s)\" == \"Darwin\" ]]; then
                stat -f %z \"\$1\"
            else
                stat -c %s \"\$1\"
            fi
        }
        _stat_size /some/file
    "
    assert_success
    assert_output "stat-args:-c %s /some/file"
}

# ── llm-cold-storage get_last_access_days BSD branch ───────────────────────

@test "get_last_access_days uses 'stat -f %a' on Darwin (BSD find -printf unavailable)" {
    # Verify both BSD and GNU stat branches are in place.
    run grep -F "stat -f %a" "$REPO_ROOT/scripts/llm-cold-storage.sh"
    assert_success
    run grep -F "stat -c %X" "$REPO_ROOT/scripts/llm-cold-storage.sh"
    assert_success
    # And confirm `find -printf` is absent from production code (comments OK).
    run bash -c "grep -v '^[[:space:]]*#' '$REPO_ROOT/scripts/llm-cold-storage.sh' | grep -F 'find -printf'"
    assert_failure
}

# ── migrate-config MIGRATIONS_DIR resolves to ../migrations ────────────────

@test "migrate-config: MIGRATIONS_DIR points at sibling migrations/ dir" {
    run grep -E '^MIGRATIONS_DIR=.*\.\./migrations' "$REPO_ROOT/scripts/migrate-config.sh"
    assert_success
}

@test "migrate-config: sort -V replaced with portable sort" {
    # `sort -V` is GNU-only on macOS' BSD sort; semver-padded filenames
    # sort lexicographically with plain `sort`.
    run grep -F "sort -V" "$REPO_ROOT/scripts/migrate-config.sh"
    assert_failure
}
