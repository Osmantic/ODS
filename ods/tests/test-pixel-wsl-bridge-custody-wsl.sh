#!/usr/bin/env bash
# Manual WSL regression: all filesystem mutations occur in a private mount
# namespace with a new tmpfs over /mnt/wsl, never on the installed ODS tree.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bridge="$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh"
grep -qi microsoft /proc/sys/kernel/osrelease
owner="${1:-$(id -un)}"
[[ "$owner" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$(id -u "$owner")" -ne 0 ]]
getent group ods-pixel >/dev/null
command -v unshare >/dev/null

scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
# Windows checkouts may have CRLF; exercise the exact source bytes as LF.
tr -d '\r' < "$bridge" > "$scratch/bridge.sh"

cat > "$scratch/check.sh" <<'CHECK'
set -euo pipefail
parent_ns="$1" bridge="$2" owner="$3"
[[ "$(readlink /proc/self/ns/mnt)" != "$parent_ns" ]]
mount --make-rprivate /
# A retained host may itself have legacy ODS mounts. Drop only copies in this
# already isolated namespace so they cannot trip this new-layout fixture.
for kind in ingress preview; do
    target="/mnt/wsl/ods-portal-runtime/$kind"
    old_root="/ods-portal-runtime/$kind"
    case "$kind" in ingress) run_root=/ods-pixel ;; preview) run_root=/ods-pixel-preview ;; esac
    count="$(awk -v path="$target" -v old_root="$old_root" -v run_root="$run_root" '
        $5 == path { count++; if (($4 != old_root && $4 != run_root) || $0 !~ / - tmpfs none /) bad = 1 }
        END { if (bad) exit 3; print count + 0 }
    ' /proc/self/mountinfo)"
    (( count <= 8 ))
    while (( count > 0 )); do
        umount -- "$target"
        (( count -= 1 )) || true
    done
done
mount -t tmpfs -o mode=1777 none /mnt/wsl
mount --make-shared /mnt/wsl
[[ "$(findmnt -n -o PROPAGATION -T /mnt/wsl | tail -n 1)" == shared ]]
# findmnt reports both the hidden original and the new overmount in some WSL
# versions. Only mock that one propagation query; real mount isolation above
# and the bridge's directory and socket checks still run unchanged.
mkdir "$(dirname "$bridge")/bin"
cat > "$(dirname "$bridge")/bin/findmnt" <<'FINDMNT'
#!/bin/sh
[ "$*" = '-n -o PROPAGATION -T /mnt/wsl' ] || exit 2
printf 'shared\n'
FINDMNT
chmod 0755 "$(dirname "$bridge")/bin/findmnt"
chown "$owner" "$(dirname "$bridge")/bin" "$(dirname "$bridge")/bin/findmnt"
PATH="$(dirname "$bridge")/bin:$PATH"
export PATH
base=/mnt/wsl/ods-portal-sockets
install -d -o root -g root -m 0755 "$base"

expect_refusal() {
    local expected="$1" output rc
    rc=0
    output="$(bash "$bridge" ensure "$owner" 2>&1)" || rc=$?
    [[ "$rc" -ne 0 && "$output" == *"$expected"* ]] || {
        printf 'expected refusal %q; rc=%s output=%s\n' "$expected" "$rc" "$output" >&2
        exit 1
    }
}

# Wrong child owner/mode must remain unchanged, even though the base is owned.
install -d -o root -g root -m 0777 "$base/ingress"
before="$(stat -c '%u:%G:%a:%i' "$base/ingress")"
expect_refusal 'unexpected ownership or mode'
[[ "$(stat -c '%u:%G:%a:%i' "$base/ingress")" == "$before" ]]
rmdir "$base/ingress"

install -d -o root -g ods-pixel -m 0710 "$base/ingress"
before="$(stat -c '%u:%G:%a:%i' "$base/ingress")"
expect_refusal 'unexpected ownership or mode'
[[ "$(stat -c '%u:%G:%a:%i' "$base/ingress")" == "$before" ]]
rmdir "$base/ingress"

# A root-owned stale socket in an otherwise valid child is not ODS-owned.
install -d -o "$owner" -g ods-pixel -m 0710 "$base/ingress"
python3 - "$base/ingress/pixel-ingress.sock" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
s.close()
PY
before="$(stat -c '%u:%i' "$base/ingress/pixel-ingress.sock")"
expect_refusal 'socket owned by another user'
[[ "$(stat -c '%u:%i' "$base/ingress/pixel-ingress.sock")" == "$before" ]]
unlink "$base/ingress/pixel-ingress.sock"
rmdir "$base/ingress"

bash "$bridge" ensure "$owner"
[[ "$(stat -c '%U:%G:%a' "$base/ingress")" == "$owner:ods-pixel:710" ]]
[[ "$(stat -c '%U:%G:%a' "$base/preview")" == "$owner:ods-pixel:750" ]]
bash "$bridge" ensure "$owner"
printf 'WSL bridge custody fixture passed in isolated mount namespace\n'
CHECK

sudo -n unshare -m -- bash "$scratch/check.sh" "$(readlink /proc/self/ns/mnt)" "$scratch/bridge.sh" "$owner"
