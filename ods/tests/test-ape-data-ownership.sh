#!/usr/bin/env bash
# Exercise shipped APE preparation with real UID changes in a disposable container.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${ODS_APE_TEST_IMAGE:-python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f}"
if ! command -v docker >/dev/null || ! docker info >/dev/null 2>&1; then
    echo 'SKIP: APE ownership behavior requires Docker'
    exit 0
fi
docker run --rm -i --network none --user 0:0 \
    --mount "type=bind,src=$ROOT_DIR/installers/phases/06-directories.sh,dst=/phase06.sh,readonly" \
    "$IMAGE" bash -s <<'CONTAINER'
set -euo pipefail
umask 022
block=$(awk '/# APE \(Agent Policy Engine\) persists private governance state/{grab=1} grab{print} grab && /^    fi$/{exit}' /phase06.sh)
[[ -n "$block" ]]
_phase06_rootless=false
error() { printf 'EXPECTED-ERROR: %s\n' "$*" >&2; return 1; }
ods_sudo_available() { return 0; }
ods_sudo() { "$@"; }
run_block() { eval "$block"; }
fixture() {
    INSTALL_DIR="/fixtures/$1"
    mkdir -p "$INSTALL_DIR/extensions/services/ape" "$INSTALL_DIR/data"
    (umask 077; mkdir "$INSTALL_DIR/data/ape")
    printf 'services:\n  ape:\n    volumes:\n      - ./data/ape:/data/ape:z\n' > "$INSTALL_DIR/extensions/services/ape/compose.yaml"
}
fixture fresh
printf 'preserve-me\n' > "$INSTALL_DIR/data/ape/state.json"
chmod 600 "$INSTALL_DIR/data/ape/state.json"
chown -R 1000:1000 "$INSTALL_DIR/data/ape"
[[ $(stat -c '%u:%g:%a' "$INSTALL_DIR/data/ape") == 1000:1000:700 ]]
run_block
[[ $(stat -c '%u:%g:%a' "$INSTALL_DIR/data/ape") == 100:65534:700 ]]
[[ $(cat "$INSTALL_DIR/data/ape/state.json") == preserve-me ]]
[[ $(stat -c '%a' "$INSTALL_DIR/data/ape/state.json") == 600 ]]
python3 - "$INSTALL_DIR/data/ape" <<'PY'
import os, pathlib, sys
os.setgroups([])
os.setgid(65534)
os.setuid(100)
p = pathlib.Path(sys.argv[1])
assert p.joinpath('state.json').read_text() == 'preserve-me\n'
p.joinpath('audit.jsonl').write_text('proof\n')
PY
echo 'PASS: restrictive umask, wrong owner, retained private state, actual APE UID write'
run_block
[[ $(cat "$INSTALL_DIR/data/ape/audit.jsonl") == proof ]]
echo 'PASS: retained rerun preserves APE state'
for link_kind in ape data install; do
    fixture "link-$link_kind"
    case "$link_kind" in
        ape) source="$INSTALL_DIR/data/ape" ;;
        data) source="$INSTALL_DIR/data" ;;
        install) source="$INSTALL_DIR" ;;
    esac
    target="/outside-$link_kind"
    mv "$source" "$target"
    ln -s "$target" "$source"
    before=$(stat -c '%u:%g:%a' "$target")
    if run_block; then echo "FAIL: accepted $link_kind symlink"; exit 1; fi
    [[ $(stat -c '%u:%g:%a' "$target") == "$before" ]]
done
echo 'PASS: bind source and parent symlinks refused without target changes'
fixture chown-failure
ods_sudo() { return 31; }
if run_block; then echo 'FAIL: ignored chown failure'; exit 1; fi
[[ $(stat -c '%u:%g:%a' "$INSTALL_DIR/data/ape") == 0:0:700 ]]
echo 'PASS: failed ownership repair aborts'
ods_sudo() { "$@"; }
fixture unmounted
printf 'services:\n  ape: {}\n' > "$INSTALL_DIR/extensions/services/ape/compose.yaml"
before=$(stat -c '%u:%g:%a' "$INSTALL_DIR/data/ape")
run_block
[[ $(stat -c '%u:%g:%a' "$INSTALL_DIR/data/ape") == "$before" ]]
echo 'PASS: no APE bind mount leaves unrelated state unchanged'
CONTAINER
