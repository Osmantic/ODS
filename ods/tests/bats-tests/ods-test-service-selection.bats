#!/usr/bin/env bats

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TEST_ROOT="${ODS_TEST_ROOT_OVERRIDE:-$(cd "$BATS_TEST_DIRNAME/../.." && pwd)}"
    export TEST_TMP="$BATS_TEST_TMPDIR/ods-test-selection"
    export TEST_BIN="$TEST_TMP/bin"
    export DOCKER_MARKER="$TEST_TMP/docker-called"
    mkdir -p "$TEST_BIN"

    cat > "$TEST_BIN/docker" <<'EOF'
#!/bin/sh
printf 'called\n' > "$DOCKER_MARKER"
case "${1:-}" in
    info) exit 0 ;;
    ps) printf 'ods-dashboard\nods-llama-server\n' ;;
esac
EOF
    cat > "$TEST_BIN/curl" <<'EOF'
#!/bin/sh
printf '200'
EOF
    cat > "$TEST_BIN/timeout" <<'EOF'
#!/bin/sh
shift
exec "$@"
EOF
    chmod +x "$TEST_BIN/docker" "$TEST_BIN/curl" "$TEST_BIN/timeout"
}

@test "repeatable and comma-separated service selections run once in request order" {
    run env \
        DOCKER_MARKER="$DOCKER_MARKER" \
        ENV_FILE="$TEST_TMP/missing.env" \
        PATH="$TEST_BIN:$PATH" \
        bash "$TEST_ROOT/scripts/ods-test.sh" --quick \
            --service docker,privacy-shield --service docker

    assert_success
    assert_output --partial "> Docker Infrastructure"
    assert_output --partial "> Privacy Shield M3"
    refute_output --partial "> GPU Resources"
    [ "$(printf '%s\n' "$output" | grep -c '> Docker Infrastructure')" -eq 1 ]

    local docker_line privacy_line
    docker_line=$(printf '%s\n' "$output" | grep -n '> Docker Infrastructure' | cut -d: -f1)
    privacy_line=$(printf '%s\n' "$output" | grep -n '> Privacy Shield M3' | cut -d: -f1)
    [ "$docker_line" -lt "$privacy_line" ]
}

@test "the complete selection is validated before any service test runs" {
    run env \
        DOCKER_MARKER="$DOCKER_MARKER" \
        ENV_FILE="$TEST_TMP/missing.env" \
        PATH="$TEST_BIN:$PATH" \
        bash "$TEST_ROOT/scripts/ods-test.sh" --service docker,unknown

    assert_failure 2
    assert_output --partial "Unknown service: unknown"
    [ ! -e "$DOCKER_MARKER" ]
}

@test "service option without a value reports a configuration error" {
    run env ENV_FILE="$TEST_TMP/missing.env" bash "$TEST_ROOT/scripts/ods-test.sh" --service

    assert_failure 2
    assert_output --partial "requires a service list"
}

@test "empty entries cannot silently expand into the all-services suite" {
    run env ENV_FILE="$TEST_TMP/missing.env" bash "$TEST_ROOT/scripts/ods-test.sh" --service docker,

    assert_failure 2
    assert_output --partial "cannot contain an empty name"
}
