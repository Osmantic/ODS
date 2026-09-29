#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../installers/lib/source-copy.sh
source "$ROOT/installers/lib/source-copy.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
error() { printf 'ERROR: %s\n' "$*" >&2; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$*"; }
src="$tmp/src"
mkdir -p "$src/config/litellm"
printf 'bundled-template\n' > "$src/config/litellm/cloud.yaml"
printf 'source-canary\n' > "$src/canary"
touch "$src/.gitignore"
setup() { inst="$tmp/$1"; mkdir -p "$inst/config/litellm"; }
copy() { ods_copy_install_source "$src" "$inst" "$tmp/log"; }
setup fresh
copy
cmp "$src/config/litellm/cloud.yaml" "$inst/config/litellm/cloud.yaml" || fail 'fresh template absent'
pass 'fresh installation copies the bundled template'
printf 'owner-provider\n' > "$inst/config/litellm/cloud.yaml"
chmod 644 "$inst/config/litellm/cloud.yaml"
before="$(stat -c '%d:%i:%u:%g:%a:%s' "$inst/config/litellm/cloud.yaml")"
exec 9< "$inst/config/litellm/cloud.yaml"
for round in 1 2; do
    copy
    [[ "$before" == "$(stat -c '%d:%i:%u:%g:%a:%s' "$inst/config/litellm/cloud.yaml")" ]] || fail 'provider identity changed'
    [[ "$(cat "$inst/config/litellm/cloud.yaml")" == owner-provider ]] || fail 'provider bytes changed'
    pass "provider preserved on rerun $round"
done
[[ "$(cat <&9)" == owner-provider ]] || fail 'open consumer saw a different provider'
exec 9<&-
pass 'two reruns preserve provider bytes, metadata, inode and an open consumer'
for fixture in leaf-link leaf-writable parent-link parent-writable root-link root-writable; do
    setup "$fixture"
    printf 'owner-provider\n' > "$inst/config/litellm/cloud.yaml"
    case "$fixture" in
        leaf-link) mv "$inst/config/litellm/cloud.yaml" "$inst/target"; ln -s ../../target "$inst/config/litellm/cloud.yaml" ;;
        leaf-writable) chmod 666 "$inst/config/litellm/cloud.yaml" ;;
        parent-link) mv "$inst/config/litellm" "$inst/config/real"; ln -s real "$inst/config/litellm" ;;
        parent-writable) chmod 777 "$inst/config/litellm" ;;
        root-link) mv "$inst" "$inst-real"; ln -s "$inst-real" "$inst" ;;
        root-writable) chmod 777 "$inst" ;;
    esac
    if copy; then fail "unsafe $fixture accepted"; fi
    [[ ! -e "$inst/canary" ]] || fail "source copied before $fixture refusal"
    pass "$fixture refuses before source copying"
done
# Installs made before phase 06 normalized code modes (v2.6.0 and earlier),
# under the user-private-group umask 002, carry group-writable directories and
# a 664 cloud.yaml. The copy check runs before that normalization, so it must
# tighten the owner's own private group bit instead of refusing every upgrade.
# A shared group keeps refusing. `id` is stubbed so the runner's groups do not
# decide the outcome.
group_writable_install() {
    setup "$1"
    printf 'owner-provider\n' > "$inst/config/litellm/cloud.yaml"
    chmod 775 "$inst" "$inst/config" "$inst/config/litellm"
    chmod 664 "$inst/config/litellm/cloud.yaml"
}
group_modes() {
    stat -c '%a' "$inst" "$inst/config" "$inst/config/litellm" "$inst/config/litellm/cloud.yaml" | tr '\n' ' '
}
group_writable_install private-group
id() { if [[ "${1:-}" == -gn ]]; then builtin command id -un; else builtin command id "$@"; fi; }
copy || fail 'user-private-group upgrade refused'
unset -f id
[[ -e "$inst/canary" ]] || fail 'user-private-group upgrade did not copy the source'
[[ "$(cat "$inst/config/litellm/cloud.yaml")" == owner-provider ]] || fail 'user-private-group upgrade changed the provider'
[[ "$(group_modes)" == '755 755 755 644 ' ]] || fail "user-private-group modes not tightened: $(group_modes)"
pass 'user-private-group modes are tightened and the upgrade proceeds'
group_writable_install shared-group
id() { if [[ "${1:-}" == -gn ]]; then printf 'users\n'; else builtin command id "$@"; fi; }
if copy; then fail 'shared-group writable provider accepted'; fi
unset -f id
[[ ! -e "$inst/canary" ]] || fail 'source copied before shared-group refusal'
[[ "$(group_modes)" == '775 775 775 664 ' ]] || fail "shared-group refusal changed modes: $(group_modes)"
pass 'shared-group writable provider refuses before source copying'
setup no-rsync
printf 'owner-provider\n' > "$inst/config/litellm/cloud.yaml"
command() { if [[ "${1:-}" == -v && "${2:-}" == rsync ]]; then return 1; fi; builtin command "$@"; }
if copy; then fail 'rerun accepted without rsync'; fi
[[ ! -e "$inst/canary" && "$(cat "$inst/config/litellm/cloud.yaml")" == owner-provider ]] || fail 'missing-rsync refusal changed existing files'
pass 'missing rsync refuses an existing provider before copying'
setup fresh-fallback
copy
cmp "$src/config/litellm/cloud.yaml" "$inst/config/litellm/cloud.yaml" || fail 'fresh fallback template absent'
pass 'fresh fallback copies the template'
unset -f command
