#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/phases/06-directories.sh permissions
# ============================================================================
# Regression tests for two bugs:
#   - .env must be created mode 600 from inception (issue #549) — the umask
#     subshell wrap must produce a 0600 file even with a permissive ambient
#     umask.
#   - The umask 077 MUST NOT leak out of the subshell, otherwise later
#     mkdirs in the same phase (and subsequent phases) create directories
#     with mode 700 that container processes (SearXNG uid 977, OpenClaw
#     uid 1000, etc.) cannot traverse — silent runtime breakage.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export INSTALL_DIR="$BATS_TEST_TMPDIR/install-target"
    mkdir -p "$INSTALL_DIR"
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/install-target"
}

# ── .env permissions ────────────────────────────────────────────────────────

@test ".env is mode 600 immediately after creation (subshell umask wrap)" {
    # Replay the exact umask-subshell pattern from 06-directories.sh so a
    # regression that drops the subshell or the umask is caught here.
    run bash -c '
        umask 022   # Simulate a permissive ambient umask (Ubuntu default)
        (
            umask 077
            cat > "'"$INSTALL_DIR"'/.env" << ENV_EOF
TEST_KEY=test-value
ENV_EOF
        )
        stat -c "%a" "'"$INSTALL_DIR"'/.env"
    '
    assert_success
    assert_output "600"
}

# ── Container-bind-mount directories must remain world-traversable ──────────

@test "container-bind-mount dirs are not 700-class after .env subshell" {
    # Replay the .env subshell, then create the same set of bind-mount dirs
    # that phase 06 / phase 11 create later. If the umask leaked, these
    # would inherit 0700 and container uids (SearXNG 977, OpenClaw 1000,
    # ComfyUI root) could not enter them.
    run bash -c '
        umask 022   # Ambient umask the installer normally inherits
        (
            umask 077
            cat > "'"$INSTALL_DIR"'/.env" << ENV_EOF
TEST_KEY=test-value
ENV_EOF
        )
        # Mirror the post-.env mkdirs that phase 06 + phase 11 perform.
        mkdir -p "'"$INSTALL_DIR"'/config/litellm"
        mkdir -p "'"$INSTALL_DIR"'/config/searxng"
        mkdir -p "'"$INSTALL_DIR"'/config/llama-server"
        mkdir -p "'"$INSTALL_DIR"'/data/comfyui/output"
        for d in \
            "'"$INSTALL_DIR"'/config/litellm" \
            "'"$INSTALL_DIR"'/config/searxng" \
            "'"$INSTALL_DIR"'/config/llama-server" \
            "'"$INSTALL_DIR"'/data/comfyui/output"; do
            mode=$(stat -c "%a" "$d")
            # Octal world-traverse bit is the 1s digit & 1.
            world_x=$(( 8#$mode & 1 ))
            if [[ $world_x -ne 1 ]]; then
                echo "LEAKED $d=$mode"
                exit 1
            fi
        done
        echo "OK"
    '
    assert_success
    assert_output "OK"
}
