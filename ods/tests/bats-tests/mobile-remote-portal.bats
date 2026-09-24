#!/usr/bin/env bats

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TEST_ROOT="${ODS_TEST_ROOT_OVERRIDE:-$(cd "$BATS_TEST_DIRNAME/../.." && pwd)}"
    export MOBILE_HOME="$BATS_TEST_TMPDIR/mobile-home"
    export MOBILE_BIN="$MOBILE_HOME/bin"
    export MOBILE_CONFIG="$MOBILE_HOME/config"
    export OPENER_BIN="$MOBILE_HOME/opener-bin"
    export OPENED_URL="$MOBILE_HOME/opened-url"
    mkdir -p "$OPENER_BIN"
}

_install_opener() {
    local name="$1"
    cat > "$OPENER_BIN/$name" <<'EOF'
#!/bin/sh
printf '%s\n' "$1" > "$OPENED_URL"
EOF
    chmod +x "$OPENER_BIN/$name"
}

@test "Termux bootstrap persists a normalized portal and installs a working launcher" {
    _install_opener termux-open-url

    run env \
        HOME="$MOBILE_HOME" \
        ODS_PLATFORM_OVERRIDE=android-termux \
        ODS_MOBILE_BIN_DIR="$MOBILE_BIN" \
        ODS_MOBILE_CONFIG_DIR="$MOBILE_CONFIG" \
        bash "$TEST_ROOT/install.sh" \
            --server https://ods.example.test --no-open

    assert_success
    run grep -Fx 'https://ods.example.test/talk' "$MOBILE_CONFIG/mobile-portal-url"
    assert_success
    [ -x "$MOBILE_BIN/ods-mobile" ]

    run env PATH="$OPENER_BIN:$PATH" OPENED_URL="$OPENED_URL" "$MOBILE_BIN/ods-mobile"
    assert_success
    run grep -Fx 'https://ods.example.test/talk' "$OPENED_URL"
    assert_success
}

@test "a-Shell bootstrap uses its native URL opener without duplicating /talk" {
    _install_opener open

    run env \
        HOME="$MOBILE_HOME" \
        PATH="$OPENER_BIN:$PATH" \
        OPENED_URL="$OPENED_URL" \
        ODS_PLATFORM_OVERRIDE=ios-ashell \
        ODS_MOBILE_BIN_DIR="$MOBILE_BIN" \
        ODS_MOBILE_CONFIG_DIR="$MOBILE_CONFIG" \
        bash "$TEST_ROOT/install.sh" \
            --server http://talk.ods.local/talk

    assert_success
    run grep -Fx 'http://talk.ods.local/talk' "$OPENED_URL"
    assert_success
}

@test "dry run resolves the plan without creating mobile state" {
    run env \
        HOME="$MOBILE_HOME" \
        ODS_PLATFORM_OVERRIDE=android-termux \
        ODS_MOBILE_BIN_DIR="$MOBILE_BIN" \
        ODS_MOBILE_CONFIG_DIR="$MOBILE_CONFIG" \
        bash "$TEST_ROOT/install.sh" \
            --server https://ods.example.test --dry-run

    assert_success
    assert_output --partial 'Dry run complete; no files were written.'
    [ ! -e "$MOBILE_BIN" ]
    [ ! -e "$MOBILE_CONFIG" ]
}

@test "non-interactive bootstrap rejects missing or credential-like URLs" {
    run env HOME="$MOBILE_HOME" ODS_PLATFORM_OVERRIDE=android-termux \
        bash "$TEST_ROOT/install.sh" --no-open
    assert_failure 2
    assert_output --partial '--server is required'

    run env HOME="$MOBILE_HOME" ODS_PLATFORM_OVERRIDE=android-termux \
        bash "$TEST_ROOT/install.sh" \
            --server 'https://ods.example.test?token=secret' --no-open
    assert_failure 2
    assert_output --partial 'cannot contain query parameters or fragments'

    run env HOME="$MOBILE_HOME" ODS_PLATFORM_OVERRIDE=android-termux \
        bash "$TEST_ROOT/install.sh" \
            --server 'https://user:password@ods.example.test' --no-open
    assert_failure 2
    assert_output --partial 'cannot embed credentials'
}
