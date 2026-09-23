#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
repo="$tmp/repo"
mkdir -p "$repo"
tar --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist \
  --exclude=__pycache__ --exclude='*.pyc' -C "$SOURCE" -cf - . | tar -xf - -C "$repo"

install_dir="$tmp/install"
openclaw_home="$tmp/openclaw"
mkdir -p "$install_dir" "$openclaw_home"
cat > "$repo/.env" <<ENV
PIXEL_INSTALL_DIR=$install_dir
OPENCLAW_HOME=$openclaw_home
PIXEL_RELEASE_VERSION=4.3.27
ENV

state="$tmp/state"
out="$tmp/out"
mkdir -p "$state" "$out"
chmod 700 "$out"

backup="$state/backup.tar.gz.age"
printf 'encrypted-fake' > "$backup"
# Malformed checksum file: not a single 64-lowercase-hex line.
printf 'not-a-valid-checksum\n' > "$backup.sha256"
printf 'fake-signature\n' > "$backup.sig"
identity="$state/identity"
printf 'age identity material' > "$identity"
chmod 600 "$identity"
signers="$state/allowed-signers"
printf 'pixel-backup ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA\n' > "$signers"
chmod 644 "$signers"

receipt="$out/receipt.json"
cd "$repo"

set +e
output=$(bash scripts/restore-private-state.sh "$backup" --identity "$identity" --signers "$signers" --confirm --receipt "$receipt" 2>&1)
status=$?
set -e

if [[ $status -eq 0 ]]; then
  echo "restore unexpectedly succeeded on a malformed checksum" >&2
  printf '%s\n' "$output" >&2
  exit 1
fi
grep -F "Backup checksum file is malformed" <<<"$output" >/dev/null \
  || { echo "restore did not fail at the expected early checksum gate" >&2; printf '%s\n' "$output" >&2; exit 1; }
if [[ -e "$receipt" || -L "$receipt" ]]; then
  echo "early-failure restore left the receipt reservation behind" >&2
  exit 1
fi

echo "restore-private-state early-failure trap cleaned the reservation"
