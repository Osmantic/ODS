#!/usr/bin/env bats

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TEST_ROOT="${ODS_TEST_ROOT_OVERRIDE:-$(cd "$BATS_TEST_DIRNAME/../.." && pwd)}"
    export TUI_TMP="$BATS_TEST_TMPDIR/tui"
    export TUI_BIN="$TUI_TMP/bin"
    export TUI_ARGS="$TUI_TMP/args"
    export TUI_INSTALL_DIR="$TUI_TMP/install-dir"
    mkdir -p "$TUI_BIN"

cat > "$TUI_BIN/bash" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "$TUI_ARGS"
printf '%s\n' "$INSTALL_DIR" > "$TUI_INSTALL_DIR"
EOF
    chmod +x "$TUI_BIN/bash"
}

@test "configuration TUI compiles a confirmed custom plan before dispatch" {
    run env \
        ODS_PLATFORM_OVERRIDE=linux \
        TUI_ARGS="$TUI_ARGS" \
        TUI_INSTALL_DIR="$TUI_INSTALL_DIR" \
        PATH="$TUI_BIN:$PATH" \
        /bin/bash "$TEST_ROOT/install.sh" --tui <<EOF
1
3
$TUI_TMP/install target
4
no
yes
no
yes
yes
no
yes
yes
no
no
yes
yes
EOF

    assert_success
    run grep -Fx -- "$TEST_ROOT/install-core.sh" "$TUI_ARGS"
    assert_success
    for expected in --non-interactive --tier --no-voice --workflows --no-rag \
        --recommended --hermes --no-comfyui --langfuse --lan --no-bootstrap; do
        run grep -Fx -- "$expected" "$TUI_ARGS"
        assert_success
    done
    run grep -Fx -- "3" "$TUI_ARGS"
    assert_success
    run grep -Fx -- "--dry-run" "$TUI_ARGS"
    assert_success
    run grep -Fx -- "$TUI_TMP/install target" "$TUI_INSTALL_DIR"
    assert_success
}

@test "configuration TUI cancellation never dispatches the installer" {
    run env \
        ODS_PLATFORM_OVERRIDE=linux \
        TUI_ARGS="$TUI_ARGS" \
        PATH="$TUI_BIN:$PATH" \
        /bin/bash "$TEST_ROOT/install.sh" --tui <<EOF
2
$TUI_TMP/cloud
2
no
yes
no
no
EOF

    assert_success
    assert_output --partial "Installation cancelled; no changes were made."
    [ ! -e "$TUI_ARGS" ]
}

@test "configuration TUI rejects ambiguous mixed CLI input" {
    run env ODS_PLATFORM_OVERRIDE=linux /bin/bash "$TEST_ROOT/install.sh" --tui --all

    assert_failure 2
    assert_output --partial "--tui cannot be combined"
}

@test "installer help remains successful when a short reader closes the pipe" {
    run /bin/bash -o pipefail -c \
        '"$1" --help 2>&1 | grep -qiE "usage|option|ods"' \
        help-probe "$TEST_ROOT/install.sh"

    assert_success

    run env ODS_PLATFORM_OVERRIDE=linux /bin/bash "$TEST_ROOT/install.sh" --help
    assert_success
    assert_output --partial "Dispatcher option: --tui"
}
