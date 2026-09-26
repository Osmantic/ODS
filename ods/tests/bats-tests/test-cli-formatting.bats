#!/usr/bin/env bats
# Behavior contract for the first ODS CLI decomposition slice.
#
# The public CLI still owns command syntax and output; lib/cli-format.sh owns
# only the pure color, JSON escaping, and separator primitives.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export CLI="$BATS_TEST_DIRNAME/../../ods-cli"
    export MODULE="$BATS_TEST_DIRNAME/../../lib/cli-format.sh"
    RED='<red>'
    GREEN='<green>'
    YELLOW='<yellow>'
    # shellcheck source=../../lib/cli-format.sh
    source "$MODULE"
}

@test "cli-format: ods-cli loads the installed formatting module" {
    run env -i \
        HOME="$BATS_TEST_TMPDIR" \
        PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/Volumes/X/homebrew/bin" \
        TERM="dumb" \
        bash "$CLI" --version
    assert_success
    refute_output --partial "lib/cli-format.sh: No such file"
}

@test "cli-format: status colors preserve the list output contract" {
    run _status_color enabled
    assert_output '<green>'
    run _status_color stopped
    assert_output '<yellow>'
    run _status_color error
    assert_output '<red>'
    run _status_color future-state
    assert_output ''
}

@test "cli-format: JSON escaping covers quotes slashes and controls" {
    run _json_escape $'quote" slash\\ line\n tab\t return\r back\b form\f'
    assert_output 'quote\" slash\\ line\n tab\t return\r back\b form\f'
}

@test "cli-format: separators preserve requested width and character" {
    run hr 4 '='
    assert_output '===='
    run hr 3
    assert_output '───'
}
