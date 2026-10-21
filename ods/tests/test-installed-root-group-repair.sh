#!/usr/bin/env bash
# Older Pixel source updates left the files and folders they wrote in group
# root. Run Phase 06's real repair as an ordinary owner against fixtures only
# root can create: the owner's root-group files and folders in the six source
# trees return to the owner's group; files of other owners, files outside
# those trees and link targets keep their group; a second run changes and
# logs nothing.
#
# The fixtures need passwordless sudo. Without it the test prints SKIP with
# the reason and exits 0, the tests/ convention for a skip; any other error
# is a FAIL.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE06="${ODS_PHASE06_UNDER_TEST:-$ROOT_DIR/installers/phases/06-directories.sh}"
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
trap 'echo "FAIL: line $LINENO: $BASH_COMMAND" >&2' ERR

repair="$(sed -n '/^    _phase06_repair_root_group() {$/,/^    }$/p' "$PHASE06")"
[[ -n "$repair" ]] || fail "phase 06 defines no _phase06_repair_root_group"
call_line="$(awk '/^    _phase06_repair_root_group$/ {print NR; exit}' "$PHASE06")"
rebind_line="$(awk '/_phase06_step "rebind-pixel-source"/ {print NR; exit}' "$PHASE06")"
[[ -n "$call_line" && -n "$rebind_line" && "$call_line" -lt "$rebind_line" ]] \
    || fail "phase 06 must run the repair before the rebind-pixel-source step"
if grep -Eq '(^|[^_[:alnum:]])(ods_)?sudo([^_[:alnum:]]|$)' <<<"$repair"; then
    fail "the repair must run as the owner, without sudo"
fi

# Skip only when passwordless sudo is unavailable, and say why.
if ! sudo_probe="$(sudo -n true 2>&1)"; then
    sudo_probe="${sudo_probe%%$'\n'*}"
    echo "SKIP: $(basename -- "$0") requires passwordless sudo (sudo -n true failed${sudo_probe:+: $sudo_probe})"
    exit 0
fi
pass "phase 06 runs the repair as the owner before the Pixel source rebind"

fixture="$(mktemp -d /tmp/ods-root-group.XXXXXXXX)"
[[ "$fixture" == /tmp/ods-root-group.* && ! -L "$fixture" ]] || fail "unsafe fixture path"
trap 'trap - ERR; sudo -n rm -rf -- "$fixture"' EXIT
chmod 755 "$fixture"

# The repair returns files to the group it runs with, as a fresh install's
# owner-run copy creates them.
if [[ "$(id -u)" == 0 ]]; then
    owner=nobody
    owner_gid="$(id -g nobody)"
    as_owner() { sudo -n -u nobody "$@"; }
else
    owner="$(id -un)"
    owner_gid="$(id -g)"
    as_owner() { "$@"; }
fi
[[ "$owner_gid" != 0 ]] || fail "the test owner's group must not be root"

# make_dir/make_file <owner> <group> <mode> <path>: exact metadata, as only
# root can set it.
make_dir() { sudo -n install -d -o "$1" -g "$2" -m "$3" -- "$4"; }
make_file() { sudo -n install -o "$1" -g "$2" -m "$3" /dev/null "$4"; }

install="$fixture/install"
make_dir "$owner" "$owner_gid" 0755 "$install"
# A fresh install's trees; scripts is absent and must simply be skipped.
for name in bin lib installers installers/lib extensions extensions/services vendor config data data/hermes; do
    make_dir "$owner" "$owner_gid" 0755 "$install/$name"
done
newline_name=$'new\nline.sh'
# What an older update left: the owner's files and new folders in group root.
make_dir "$owner" root 0755 "$install/extensions/services/hermes"
make_dir "$owner" root 0755 "$install/vendor/pixel"
repaired=(
    "$install/extensions/services/hermes"
    "$install/vendor/pixel"
)
for path in \
    extensions/services/hermes/cli-config.yaml.template \
    extensions/services/hermes/compose.yaml.disabled \
    vendor/pixel/VERSION \
    bin/tool.py \
    installers/lib/helper.sh \
    "lib/name with spaces.sh" \
    "lib/$newline_name"; do
    make_file "$owner" root 0755 "$install/$path"
    repaired+=("$install/$path")
done
# Files and folders of another owner stay in group root.
make_file root root 0644 "$install/bin/cache.pyc"
make_dir root root 0755 "$install/extensions/services/root-folder"
# The owner's files outside the six trees keep their group.
make_file "$owner" root 0600 "$install/.env"
make_file "$owner" root 0644 "$install/config/settings.yaml"
make_file "$owner" root 0600 "$install/data/hermes/config.yaml"
make_dir "$owner" root 0755 "$install/models"
# A link inside a tree never reaches its target.
make_dir "$owner" "$owner_gid" 0755 "$fixture/outside"
make_file "$owner" root 0644 "$fixture/outside/target"
as_owner ln -s "$fixture/outside/target" "$install/bin/link"
kept_root=(
    "$install/bin/cache.pyc"
    "$install/extensions/services/root-folder"
    "$install/.env"
    "$install/config/settings.yaml"
    "$install/data/hermes/config.yaml"
    "$install/models"
    "$fixture/outside/target"
)

log_file="$fixture/repair.log"
make_file "$owner" "$owner_gid" 0644 "$log_file"
{
    printf '%s\n' 'set -euo pipefail' \
        'log() { printf "%s\n" "$*" >> "$REPAIR_LOG"; }' \
        'error() { printf "error: %s\n" "$*" >&2; exit 23; }'
    printf '%s\n' "$repair" '_phase06_repair_root_group'
} > "$fixture/probe.sh"
chmod 644 "$fixture/probe.sh"
run_repair() {
    local status=0
    as_owner env INSTALL_DIR="$install" REPAIR_LOG="$log_file" bash "$fixture/probe.sh" || status=$?
    [[ "$status" == 0 ]] || fail "the repair stopped with exit $status"
}
group_of() { sudo -n stat -c %g -- "$1"; }
metadata() { sudo -n find "$fixture" -printf '%p %U %G %m %l\n' | LC_ALL=C sort; }

run_repair
for path in "${repaired[@]}"; do
    [[ "$(group_of "$path")" == "$owner_gid" ]] \
        || fail "still in group root: ${path#"$fixture"/}"
done
pass "the owner's files and folders in the six source trees return to the owner's group"
for path in "${kept_root[@]}"; do
    [[ "$(group_of "$path")" == 0 ]] \
        || fail "group changed outside the repair: ${path#"$fixture"/}"
done
[[ "$(sudo -n readlink -- "$install/bin/link")" == "$fixture/outside/target" ]] \
    || fail "the link inside the trees was replaced"
pass "other owners, files outside the six trees and link targets keep group root"
[[ "$(grep -c '' "$log_file")" == 1 ]] || fail "expected one log line, got: $(cat "$log_file")"
grep -Eq "(^|[^0-9])${#repaired[@]}([^0-9]|\$)" "$log_file" \
    || fail "the log line does not report ${#repaired[@]} repaired paths: $(cat "$log_file")"
pass "one log line reports the ${#repaired[@]} repaired paths"

before="$(metadata)"
run_repair
[[ "$(metadata)" == "$before" ]] || fail "a second run changed files"
[[ "$(grep -c '' "$log_file")" == 1 ]] || fail "a second run logged: $(tail -n 1 "$log_file")"
pass "a second run changes and logs nothing"
