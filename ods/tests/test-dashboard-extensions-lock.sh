#!/usr/bin/env bash
# The host CLI and the Dashboard (1000:1000) share data/.extensions-lock. It
# must stay group-1000 read/write whatever the install owner's UID.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf -- "${work:?}"' EXIT
PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }

# shellcheck source=../installers/lib/dashboard-data.sh
source "$ROOT_DIR/installers/lib/dashboard-data.sh"

# Match this shell fixture's simulated group-1000 authority, without requiring
# the CI account to belong to that group. Root numeric tests cover real IDs.
python3() {
    if [[ "$1" == "$ROOT_DIR/installers/lib/dashboard-lock.py" && "$2" == repair ]]; then
        command python3 - "$1" "$3" <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("lock_fixture", sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper._DASHBOARD_GID = os.getgid()
helper.repair(sys.argv[2])
PY
        local result=$?
        [[ "$result" == 0 ]] || return "$result"
        printf '1000\n' > "$work/groups/.extensions-lock"
    else
        command python3 "$@"
    fi
}

# Without root, chgrp to 1000 is recorded per path and read back by stat.
fake_group() { cat "$work/groups/$(basename "$1")" 2>/dev/null || command stat -c %g "$1"; }
stat() {
    if [[ "$1" == -c && "$2" == '%g' ]]; then
        fake_group "$3"
    elif [[ "$1" == -c && "$2" == '%g:%a' ]]; then
        printf '%s:%s\n' "$(fake_group "$3")" "$(command stat -c %a "$3")"
    else
        command stat "$@"
    fi
}
ods_sudo_available() { return 0; }
ods_sudo() {
    printf '%s\n' "$*" >> "$work/calls"
    case "$1" in
        chgrp) printf '%s\n' "$2" > "$work/groups/$(basename "$3")" ;;
        chmod) command chmod "$2" "$3" ;;
        *) return 91 ;;
    esac
}

for scenario in missing owner-only symlink ready; do
    rm -rf -- "${work:?}/ods" "$work/groups" "$work/calls"
    mkdir -p "$work/ods/data" "$work/groups"
    : > "$work/calls"
    lock="$work/ods/data/.extensions-lock"
    printf '1000\n' > "$work/groups/data"
    command chmod 770 "$work/ods/data"
    case "$scenario" in
        owner-only) : > "$lock"; command chmod 600 "$lock" ;;
        symlink) ln -s /etc/hostname "$lock" ;;
        ready) : > "$lock"; command chmod 660 "$lock"; printf '1000\n' > "$work/groups/.extensions-lock" ;;
    esac
    result=0
    ods_prepare_dashboard_data "$work/ods" false 2>/dev/null || result=$?
    case "$scenario" in
        symlink)
            if [[ "$result" != 0 && -L "$lock" && ! -s "$work/calls" ]]; then
                pass "phase 06 refuses a symlinked extensions lock"
            else
                fail "phase 06 accepted a symlinked extensions lock"
            fi ;;
        ready)
            if [[ "$result" == 0 && ! -s "$work/calls" ]]; then
                pass "phase 06 leaves a ready extensions lock alone"
            else
                fail "phase 06 changed a ready extensions lock: $(cat "$work/calls")"
            fi ;;
        *)
            if [[ "$result" == 0 && -f "$lock" && ! -L "$lock" \
                && "$(command stat -c %a "$lock")" == 660 \
                && "$(fake_group "$lock")" == 1000 \
                && "$(command stat -c %u "$lock")" == "$(id -u)" ]]; then
                pass "phase 06 gives a $scenario extensions lock to the owner and group 1000"
            else
                fail "phase 06 left a $scenario extensions lock unusable by the Dashboard (rc=$result, mode=$(command stat -c %a "$lock" 2>/dev/null))"
            fi ;;
    esac
done

# The host helper restores group read/write on a lock it owns, under umask 022.
for scenario in new owner-only; do
    rm -rf -- "${work:?}/cli"
    mkdir -p "$work/cli/data"
    [[ "$scenario" == new ]] || { : > "$work/cli/data/.extensions-lock"; command chmod 600 "$work/cli/data/.extensions-lock"; }
    if (umask 022 && python3 - "$ROOT_DIR/scripts/extension-selection.py" "$work/cli" <<'PY'
import importlib.util, pathlib, stat, sys
spec = importlib.util.spec_from_file_location("selection", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
install = pathlib.Path(sys.argv[2])
with module._selection_lock(install, 5):
    pass
mode = stat.S_IMODE((install / "data/.extensions-lock").stat().st_mode)
raise SystemExit(0 if mode & 0o060 == 0o060 and not mode & 0o007 else f"mode {oct(mode)}")
PY
    ); then
        pass "the host helper keeps a $scenario lock group read/write"
    else
        fail "the host helper left a $scenario lock without group read/write"
    fi
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" == 0 ]]
