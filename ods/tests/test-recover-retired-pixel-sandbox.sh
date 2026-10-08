#!/usr/bin/env bash
# Exercise recovery through a pseudo-terminal with a Docker fixture only.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/recover-retired-pixel-sandbox.sh"
SCRIPT_COMMAND=$(command -v script) || { echo 'The util-linux script command is required.' >&2; exit 1; }
TEST_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_ROOT"' EXIT
old_id="sha256:$(printf 'a%.0s' {1..64})"
other_id="sha256:$(printf 'b%.0s' {1..64})"
retained="pixel-sandbox-retained:sha256-${old_id#sha256:}"
checks=0

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

make_fixture() {
    local fixture_dir="$1"
    mkdir -p "$fixture_dir/bin" "$fixture_dir/account/.local/share/pixel"
    printf '%s\n' "$old_id" >"$fixture_dir/live-id"
    # Fixture the state path, without modifying HOME or consulting a live account.
    sed 's|${HOME:?}/.local/share/pixel|${RECOVERY_FIXTURE_HOME:?}/.local/share/pixel|' \
        "$TARGET" >"$fixture_dir/recover.sh"
    grep -Fq '${RECOVERY_FIXTURE_HOME:?}/.local/share/pixel' "$fixture_dir/recover.sh" \
        || fail 'The fixture state path was not replaced.'
    cat >"$fixture_dir/bin/id" <<'EOF'
#!/bin/bash
[[ "$*" == '-u' ]] || exit 95
if [[ "$RECOVERY_CASE" == root-account ]]; then printf '0\n'; else printf '1001\n'; fi
EOF
    cat >"$fixture_dir/bin/docker" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >>"$RECOVERY_FIXTURE/docker.log"
old_id="sha256:$(printf 'a%.0s' {1..64})"
other_id="sha256:$(printf 'b%.0s' {1..64})"
retained="pixel-sandbox-retained:sha256-${old_id#sha256:}"
live='openclaw-sandbox:bookworm-slim'
counter() {
    local path="$RECOVERY_FIXTURE/$1-count" value=0
    [[ ! -f "$path" ]] || read -r value <"$path"
    value=$((value + 1))
    printf '%s\n' "$value" >"$path"
    printf '%s\n' "$value"
}
case "${1:-}" in
    info)
        count=$(counter info)
        [[ "$RECOVERY_CASE" != engine-failed ]] || exit 92
        if [[ ( "$RECOVERY_CASE" == engine-changed && $count -ge 2 ) \
            || ( "$RECOVERY_CASE" == engine-changed-final && $count -ge 3 ) ]]; then
            printf 'engine-B\n'
        elif [[ "$RECOVERY_CASE" == engine-malformed ]]; then printf 'bad engine\n'
        else printf 'engine-A\n'; fi
        ;;
    ps)
        count=$(counter consumers)
        [[ "$RECOVERY_CASE" != consumers-failed ]] || exit 92
        if [[ "$RECOVERY_CASE" == stopped-consumer \
            || ( "$RECOVERY_CASE" == consumer-appeared && $count -ge 2 ) \
            || ( "$RECOVERY_CASE" == consumer-appeared-final && $count -ge 3 ) ]]; then
            printf '%s old-pixel Exited (0) 1 day ago\n' "${old_id#sha256:}"
        fi
        ;;
    image)
        case "${2:-}" in
            ls)
                reference="${*: -1}"
                [[ "$RECOVERY_CASE" != list-failed ]] || exit 92
                if [[ "$reference" == "reference=$live" ]]; then
                    [[ ! -f "$RECOVERY_FIXTURE/live-id" ]] || cat "$RECOVERY_FIXTURE/live-id"
                    if [[ "$RECOVERY_CASE" == list-failed-after-remove && ! -f "$RECOVERY_FIXTURE/live-id" ]]; then exit 92; fi
                elif [[ "$reference" == "reference=$retained" ]]; then
                    [[ "$RECOVERY_CASE" != retention-list-failed ]] || exit 92
                    [[ ! -f "$RECOVERY_FIXTURE/retained-id" ]] || cat "$RECOVERY_FIXTURE/retained-id"
                else exit 95; fi
                ;;
            inspect)
                reference="${*: -1}"
                [[ "$RECOVERY_CASE" != inspect-failed ]] || exit 92
                if [[ "$reference" == "$live" ]]; then
                    count=$(counter inspect-live)
                    if [[ ( "$RECOVERY_CASE" == tag-changed && $count -ge 2 ) \
                        || ( "$RECOVERY_CASE" == tag-changed-final && $count -ge 3 ) ]]; then
                        printf '%s\n' "$other_id" >"$RECOVERY_FIXTURE/live-id"
                    fi
                    image_id=$(cat "$RECOVERY_FIXTURE/live-id")
                elif [[ "$reference" == "$retained" ]]; then
                    [[ "$RECOVERY_CASE" != retention-inspect-failed ]] || exit 92
                    image_id=$(cat "$RECOVERY_FIXTURE/retained-id")
                else exit 95; fi
                version='4.3.29'; owner_uid=1000; image_user=sandbox
                case "$RECOVERY_CASE" in
                    malformed-id) image_id='sha256:short' ;;
                    foreign-image) version='<no value>'; owner_uid='<no value>' ;;
                    malformed-version) version='4.3.29-custom' ;;
                    root-image) owner_uid=0 ;;
                    malformed-uid) owner_uid='1000x' ;;
                    wrong-user) image_user=root ;;
                esac
                printf '%s|%s|%s|%s\n' "$image_id" "$version" "$owner_uid" "$image_user"
                ;;
            tag)
                [[ "$#" == 4 && "$3" == "$old_id" && "$4" == "$retained" ]] || exit 95
                [[ "$RECOVERY_CASE" != tag-failed ]] || exit 92
                printf '%s\n' "$old_id" >"$RECOVERY_FIXTURE/retained-id"
                ;;
            rm)
                [[ "$#" == 4 && "$3" == -- && "$4" == "$live" ]] || exit 95
                [[ "$RECOVERY_CASE" != remove-failed ]] || exit 92
                rm -f -- "$RECOVERY_FIXTURE/live-id"
                ;;
            *) exit 95 ;;
        esac
        ;;
    *) exit 95 ;;
esac
EOF
    chmod +x "$fixture_dir/bin/docker" "$fixture_dir/bin/id"
}

run_case() {
    local scenario="$1" expected="$2" answer="${3-RETIRED}" fixture_dir="$TEST_ROOT/$1"
    local fixture_path status
    make_fixture "$fixture_dir"
    case "$scenario" in
        retained-exists) printf '%s\n' "$old_id" >"$fixture_dir/retained-id" ;;
        retention-conflict) printf '%s\n' "$other_id" >"$fixture_dir/retained-id" ;;
        local-current) ln -s missing-release "$fixture_dir/account/.local/share/pixel/current" ;;
        local-attestation) touch "$fixture_dir/account/.local/share/pixel/runtime-attestation.json" ;;
        local-retired-attestation) ln -s missing-attestation "$fixture_dir/account/.local/share/pixel/.ods-uninstall-runtime-attestation" ;;
        absent-tag) rm -- "$fixture_dir/live-id" ;;
        no-docker) rm -- "$fixture_dir/bin/docker" ;;
    esac
    fixture_path="$fixture_dir/bin:/usr/bin:/bin"
    [[ "$scenario" != no-docker ]] || fixture_path="$fixture_dir/bin"
    status=0
    if [[ "$scenario" == non-interactive ]]; then
        RECOVERY_CASE="$scenario" RECOVERY_FIXTURE="$fixture_dir" \
            RECOVERY_FIXTURE_HOME="$fixture_dir/account" PATH="$fixture_path" \
            /bin/bash "$fixture_dir/recover.sh" </dev/null >"$fixture_dir/output" 2>&1 || status=$?
    else
        # script allocates an actual PTY; production has no test-only bypass.
        { if [[ "$scenario" == eof-confirmation ]]; then printf '\004'; else printf '%s\n' "$answer"; fi; } \
            | RECOVERY_CASE="$scenario" RECOVERY_FIXTURE="$fixture_dir" \
            RECOVERY_FIXTURE_HOME="$fixture_dir/account" PATH="$fixture_path" \
            "$SCRIPT_COMMAND" --quiet --return --command "/bin/bash '$fixture_dir/recover.sh'" /dev/null \
            >"$fixture_dir/output" 2>&1 || status=$?
    fi
    if [[ "$expected" == success ]]; then
        [[ $status == 0 ]] || { cat "$fixture_dir/output"; fail "$scenario failed"; }
        [[ ! -f "$fixture_dir/live-id" && $(cat "$fixture_dir/retained-id") == "$old_id" ]] \
            || fail "$scenario did not preserve the image and remove only the shared tag"
        grep -Fxq "image rm -- openclaw-sandbox:bookworm-slim" "$fixture_dir/docker.log" \
            || fail "$scenario did not remove the exact shared tag"
        if [[ "$scenario" == retained-exists ]]; then
            ! grep -q '^image tag ' "$fixture_dir/docker.log" || fail 'An existing retention tag was overwritten.'
        fi
    elif [[ "$expected" == absent ]]; then
        [[ $status == 0 ]] || { cat "$fixture_dir/output"; fail 'An absent tag prevented continuing installation.'; }
        ! grep -Eq '^image (tag|rm) ' "$fixture_dir/docker.log" || fail 'An absent tag caused mutation.'
    else
        [[ $status != 0 ]] || { cat "$fixture_dir/output"; fail "$scenario was accepted"; }
        if [[ "$expected" != remove-attempt ]]; then
            ! grep -q '^image rm ' "$fixture_dir/docker.log" 2>/dev/null \
                || fail "$scenario removed a tag despite rejected evidence"
        fi
        if [[ "$expected" == reject-no-mutation ]]; then
            ! grep -Eq '^image (tag|rm) ' "$fixture_dir/docker.log" 2>/dev/null \
                || fail "$scenario changed a tag before valid confirmation/evidence"
        fi
    fi
    if [[ -f "$fixture_dir/docker.log" ]]; then
        ! grep -Eq '(--force|prune|image rm .*sha256:|image tag .*candidate)' "$fixture_dir/docker.log" \
            || fail "$scenario attempted broad cleanup or candidate activation"
    fi
    checks=$((checks + 1))
    printf 'PASS %s\n' "$scenario"
}

run_case success success
run_case retained-exists success
run_case absent-tag absent
run_case no-confirmation reject-no-mutation 'NO'
run_case empty-confirmation reject-no-mutation ''
run_case eof-confirmation reject-no-mutation
run_case non-interactive reject-no-mutation
for scenario in no-docker root-account stopped-consumer engine-changed tag-changed retention-conflict \
    malformed-id foreign-image malformed-version root-image malformed-uid wrong-user engine-failed \
    engine-malformed list-failed inspect-failed consumers-failed retention-list-failed \
    consumer-appeared local-current local-attestation local-retired-attestation; do
    run_case "$scenario" reject-no-mutation
done
for scenario in engine-changed-final tag-changed-final consumer-appeared-final retention-inspect-failed tag-failed; do
    run_case "$scenario" reject
done
run_case remove-failed remove-attempt
run_case list-failed-after-remove remove-attempt
printf '%s recovery checks passed.\n' "$checks"
