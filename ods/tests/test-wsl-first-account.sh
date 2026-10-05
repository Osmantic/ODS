#!/bin/sh
# Exercise the pre-Python wsl.conf writer with real POSIX utilities.
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
writer="$root/installers/windows/lib/wsl-conf.sh"
config="$fixture/wsl.conf"
sh "$writer" "$config" user default maria boot systemd true
test "$(stat -c %a "$config")" = 644
grep -qx 'default = maria' "$config"
grep -qx 'systemd = true' "$config"
cat > "$config" <<'CONF'
# Preserve custom mounts and networking.
[automount]
options = "metadata,umask=22"
[user]
default=root
[network]
hostname=workstation
[boot]
command = service ssh start
systemd=false
systemd=false
CONF
chmod 640 "$config"
sh "$writer" "$config" user default maria boot systemd true
test "$(stat -c %a "$config")" = 640
grep -qx '# Preserve custom mounts and networking.' "$config"
grep -qx 'options = "metadata,umask=22"' "$config"
grep -qx 'hostname=workstation' "$config"
grep -qx 'command = service ssh start' "$config"
test "$(grep -c '^systemd = true$' "$config")" = 1
cp "$config" "$fixture/expected"
sh "$writer" "$config" user default maria boot systemd true
cmp "$config" "$fixture/expected"
ln -s "$config" "$fixture/link"
if sh "$writer" "$fixture/link" boot systemd true; then
    echo 'FAIL: symlink configuration accepted' >&2; exit 1
fi
cmp "$config" "$fixture/expected"
echo 'PASS: first-account configuration needs no Python, preserves settings, and is idempotent'
