#!/usr/bin/env bash
set -euo pipefail
# The retained provider fixture starts with safe private parents. Unsafe mode
# cases below set their own explicit modes, independent of the caller's umask.
umask 022
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
touch "$src/.gitignore" "$src/.dockerignore"
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
[[ -f "$inst/.dockerignore" ]] || fail 'fresh fallback dropped the root .dockerignore'
pass 'fresh fallback copies the root .dockerignore'
unset -f command

# Exercise the actual Phase 06 normalization after a copy from a checkout
# whose source modes all appear writable, as on a Windows-mounted WSL path.
phase="$ROOT/installers/phases/06-directories.sh"
normalization="$(sed -n '/^    # A Windows-mounted WSL checkout can surface every source entry as 0777\./,/^    unset _installed_code_root$/p' "$phase")"
[[ "$normalization" == *'unset _installed_code_root'* ]] || fail 'Phase 06 source mode normalization missing'
normalize() { INSTALL_DIR="$1"; eval "$normalization"; }
mode_src="$tmp/mode-src"
mode_inst="$tmp/mode-inst"
mkdir -p "$mode_src/config/litellm" "$mode_inst/data/private"
for root in bin lib scripts installers config extensions vendor; do
    mkdir -p "$mode_src/$root/subdir"
    printf 'script\n' > "$mode_src/$root/subdir/executable.sh"
    printf 'text\n' > "$mode_src/$root/subdir/plain.txt"
    chmod 777 "$mode_src/$root" "$mode_src/$root/subdir" "$mode_src/$root/subdir/executable.sh"
    chmod 666 "$mode_src/$root/subdir/plain.txt"
done
printf 'bundled-template\n' > "$mode_src/config/litellm/cloud.yaml"
chmod 777 "$mode_src/config/litellm" "$mode_src/config/litellm/cloud.yaml"
printf 'outside\n' > "$tmp/outside"
chmod 666 "$tmp/outside"
ln -s "$tmp/outside" "$mode_src/vendor/subdir/outside-link"
printf 'owner-data\n' > "$mode_inst/data/private/retained"
chmod 666 "$mode_inst/data/private/retained"
ods_copy_install_source "$mode_src" "$mode_inst" "$tmp/log" || fail 'writable-mode source copy failed'
normalize "$mode_inst" || fail 'Phase 06 mode normalization failed'
for root in bin lib scripts installers config extensions vendor; do
    [[ "$(stat -c %a "$mode_inst/$root")" == 755 ]] || fail "$root directory stayed writable"
    [[ "$(stat -c %a "$mode_inst/$root/subdir")" == 755 ]] || fail "$root nested directory stayed writable"
    [[ "$(stat -c %a "$mode_inst/$root/subdir/executable.sh")" == 755 ]] || fail "$root executable stayed writable"
    [[ "$(stat -c %a "$mode_inst/$root/subdir/plain.txt")" == 644 ]] || fail "$root plain file mode changed unexpectedly"
done
[[ "$(stat -c %a "$tmp/outside")" == 666 ]] || fail 'normalization followed a source symlink'
[[ "$(stat -c %a "$mode_inst/data/private/retained")" == 666 ]] || fail 'normalization changed retained data'
pass 'fresh copy protects every Pixel source root without changing retained data or symlink targets'

printf 'owner-provider\n' > "$mode_inst/config/litellm/cloud.yaml"
chmod 600 "$mode_inst/config/litellm/cloud.yaml"
cloud_before="$(stat -c '%d:%i:%u:%g:%a:%s' "$mode_inst/config/litellm/cloud.yaml")"
ods_copy_install_source "$mode_src" "$mode_inst" "$tmp/log" || fail 'provider-preserving mode rerun failed'
normalize "$mode_inst" || fail 'Phase 06 mode normalization on rerun failed'
[[ "$cloud_before" == "$(stat -c '%d:%i:%u:%g:%a:%s' "$mode_inst/config/litellm/cloud.yaml")" ]] || fail 'provider identity changed on mode rerun'
[[ "$(cat "$mode_inst/config/litellm/cloud.yaml")" == owner-provider ]] || fail 'provider bytes changed on mode rerun'
[[ "$(stat -c %a "$mode_inst/installers")" == 755 && "$(stat -c %a "$mode_inst/vendor")" == 755 ]] || fail 'mode rerun left Pixel roots writable'
pass 'rerun preserves private provider identity and protects installer and vendor roots'
