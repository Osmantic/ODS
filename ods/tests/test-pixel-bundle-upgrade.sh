#!/usr/bin/env bash
# Exercise the real pre-copy source selection with disposable install fixtures.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/pixel-integration.sh"
source "$ROOT/lib/safe-env.sh"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/ods-pixel-bundle-upgrade.XXXXXX")"
trap 'rm -rf -- "$fixture"' EXIT
old_ref=817214d5ec3d8aa583fe50c1dc7561f3c1a16dff
phase_pre_copy="$(sed -n '/^    _env_existing=""/,/^    unset _phase06_pixel_marker _phase06_pixel_source_transition/p' \
    "$ROOT/installers/phases/06-directories.sh")"
[[ -n "$phase_pre_copy" ]]
run_phase() { eval "$phase_pre_copy"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

# A clean local developer checkout must keep its source and pin on an upgrade.
mkdir -p "$fixture/developer/checkout"
git -C "$fixture/developer/checkout" init -q
git -C "$fixture/developer/checkout" -c user.name=Fixture -c user.email=fixture@example.invalid \
    -c commit.gpgsign=false commit --allow-empty -qm fixture
local_ref="$(git -C "$fixture/developer/checkout" rev-parse HEAD)"

probe() (
    scenario="$1"
    INSTALL_DIR="$fixture/$scenario/install"
    HOME="$fixture/$scenario/home"
    SCRIPT_DIR="$ROOT"
    ENABLE_PIXEL_RUNTIME=true
    unset PIXEL_SOURCE_URL PIXEL_SOURCE_REF PIXEL_SOURCE_DIR
    mkdir -p "$INSTALL_DIR" "$HOME/.config/ods"
    printf 'PIXEL_SOURCE_URL=bundled\nPIXEL_SOURCE_REF=%s\n' "$old_ref" > "$INSTALL_DIR/.env"
    printf '%s\n' "$old_ref" > "$HOME/.config/ods/pixel-managed.json"
    printf 'installed code must remain intact\n' > "$INSTALL_DIR/installed-sentinel"
    expected_ref="$ODS_PIXEL_BUNDLED_REF"
    expected_source=bundled
    expected_result=success
    case "$scenario" in
        inherited-public-bundle) ;;
        explicit-old-pin)
            PIXEL_SOURCE_REF="$old_ref"
            expected_result=failure ;;
        unknown-persisted-pin)
            printf 'PIXEL_SOURCE_URL=bundled\nPIXEL_SOURCE_REF=%040d\n' 0 > "$INSTALL_DIR/.env"
            expected_result=failure ;;
        explicit-local-source|persisted-local-source)
            expected_ref="$local_ref"
            expected_source="$fixture/developer/checkout"
            if [[ "$scenario" == explicit-local-source ]]; then
                PIXEL_SOURCE_URL="$expected_source"
                PIXEL_SOURCE_REF="$expected_ref"
                PIXEL_SOURCE_DIR="$fixture/developer"
            else
                printf 'PIXEL_SOURCE_URL=%s\nPIXEL_SOURCE_REF=%s\nPIXEL_SOURCE_DIR=%s\n' \
                    "$expected_source" "$expected_ref" "$fixture/developer" > "$INSTALL_DIR/.env"
            fi ;;
        missing-bundle|tampered-bundle|symlink-bundle)
            SCRIPT_DIR="$fixture/$scenario/new-source"
            mkdir -p "$SCRIPT_DIR/vendor"
            case "$scenario" in
                tampered-bundle) printf 'untrusted replacement\n' > "$SCRIPT_DIR/vendor/pixel.bundle" ;;
                symlink-bundle) ln -s "$ROOT/vendor/pixel.bundle" "$SCRIPT_DIR/vendor/pixel.bundle" ;;
            esac
            expected_result=failure ;;
        *) fail "unknown fixture: $scenario" ;;
    esac
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/env-before"
    ai() { :; }
    error() { printf '%s\n' "$*" >&2; }
    _phase06_step() { :; }
    ods_pixel_install_owner() { id -un; }
    ods_pixel_owner_home() { printf '%s\n' "$HOME"; }
    # Observe transition ordering without uninstalling services or owner data.
    _ods_pixel_source_transition_required() {
        [[ "$3" == "$expected_ref" ]]
    }
    _ods_pixel_restore_transition_source() {
        [[ "$3" == "$expected_ref" \
            && "$(cat "$HOME/.config/ods/pixel-managed.json")" == "$old_ref" ]] || return 1
        : > "$INSTALL_DIR/prior-source-verified"
    }
    ods_pixel_uninstall_managed() {
        [[ -f "$INSTALL_DIR/prior-source-verified" ]] || return 1
        : > "$INSTALL_DIR/retired"
    }
    if run_phase > "$INSTALL_DIR/probe.log" 2>&1; then
        [[ "$expected_result" == success ]] || fail "$scenario unexpectedly accepted"
        [[ "$_phase06_requested_pixel_ref" == "$expected_ref" \
            && "$_phase06_requested_pixel_url" == "$expected_source" \
            && -f "$INSTALL_DIR/retired" ]] || fail "$scenario lost the source contract or transition"
        if [[ "$expected_source" != bundled ]]; then
            [[ "$_phase06_requested_pixel_dir" == "$fixture/developer" ]] || fail 'local owner root changed'
        fi
    else
        [[ "$expected_result" == failure ]] || { cat "$INSTALL_DIR/probe.log" >&2; fail "$scenario failed"; }
        [[ ! -e "$INSTALL_DIR/retired" && ! -e "$INSTALL_DIR/prior-source-verified" ]] \
            || fail "$scenario touched the previous runtime before verifying the new source"
    fi
    cmp "$INSTALL_DIR/.env" "$INSTALL_DIR/env-before" || fail "$scenario rewrote the installed environment before copy"
    [[ "$(cat "$INSTALL_DIR/installed-sentinel")" == 'installed code must remain intact' ]] \
        || fail "$scenario changed installed code"
    printf '[PASS] %s\n' "$scenario"
)

for scenario in inherited-public-bundle explicit-old-pin unknown-persisted-pin \
    explicit-local-source persisted-local-source missing-bundle tampered-bundle symlink-bundle; do
    probe "$scenario"
done
