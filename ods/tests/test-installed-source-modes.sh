#!/usr/bin/env bash
# Exercise Phase 06's real normalization block as an ordinary owner. Safe
# root-created Python caches need no chmod; unsafe foreign files still fail.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
fixture=$(mktemp -d /tmp/ods-source-modes.XXXXXXXX)
[[ "$fixture" == /tmp/ods-source-modes.* && ! -L "$fixture" ]]
trap 'sudo -n rm -rf -- "$fixture"' EXIT
chmod 755 "$fixture"
root="$fixture/install"
mkdir -p "$root"/{bin,lib,scripts,config,extensions}
chmod 777 "$root" "$root/lib"
printf 'error() { echo "$*" >&2; exit 23; }\n' > "$fixture/probe.sh"
awk '/^    for _installed_code_root in /{active=1} active{print} /^    unset _installed_code_root$/{exit}' \
  "$source_dir/installers/phases/06-directories.sh" >> "$fixture/probe.sh"
grep -q 'Could not secure installed code tree' "$fixture/probe.sh"
if [[ $(id -u) == 0 ]]; then
  owner=nobody
  chown -R nobody:nogroup "$root"
  run_probe() { sudo -n -u nobody env INSTALL_DIR="$root" bash "$fixture/probe.sh"; }
else
  owner=$(id -un)
  run_probe() { env INSTALL_DIR="$root" bash "$fixture/probe.sh"; }
fi
sudo -n install -o root -g root -m 0644 /dev/null "$root/bin/safe.pyc"
sudo -n install -o "$owner" -m 0777 /dev/null "$root/bin/owned"
sudo -n install -o "$owner" -m 0777 /dev/null "$root/ods-cli"
touch "$fixture/unrelated"
chmod 666 "$fixture/unrelated"
ln -s "$fixture/unrelated" "$root/bin/unrelated-link"
run_probe
[[ $(stat -c %a "$root/bin/owned") == 755 && $(stat -c %a "$root/ods-cli") == 755 ]]
[[ $(stat -c %a "$root") == 755 && $(stat -c %a "$root/lib") == 755 ]]
[[ $(stat -c %a "$root/bin/safe.pyc") == 644 && $(stat -c %u "$root/bin/safe.pyc") == 0 ]]
[[ $(stat -c %a "$fixture/unrelated") == 666 ]]
echo 'PASS ordinary owner secures copied code and preserves safe root caches and unrelated link targets'
sudo -n install -o root -g root -m 0666 /dev/null "$root/bin/unsafe.pyc"
code=0
run_probe >/dev/null 2>&1 || code=$?
[[ "$code" == 23 && $(stat -c %a "$root/bin/unsafe.pyc") == 666 ]]
echo 'PASS an unsafe foreign-owned file stops setup'
