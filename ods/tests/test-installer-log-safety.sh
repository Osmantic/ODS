#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TMP_DIR"' EXIT

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

# Validator failures are copied into the installer log. No input line or
# invalid value may be repeated there, even if it is not a schema secret.
marker='ODS_FAKE_PRIVATE_TOKEN_7e3f5c'
printf '%s\n' \
    '{"properties":{"MODE":{"type":"string","enum":["safe"]},"COUNT":{"type":"integer"},"FLAG":{"type":"boolean"}}}' \
    >"$TMP_DIR/schema.json"
printf '%s\n' \
    "UNPARSEABLE_$marker" \
    "MODE=$marker" \
    "COUNT=$marker" \
    "FLAG=$marker" \
    >"$TMP_DIR/invalid.env"
if bash "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/invalid.env" "$TMP_DIR/schema.json" \
    >"$TMP_DIR/validator.out" 2>&1; then
    fail 'invalid environment passed validation'
else
    [[ $? -eq 2 ]] || fail 'validator failed for the wrong reason'
fi
if grep -Fq "$marker" "$TMP_DIR/validator.out"; then
    fail 'validator copied a raw private value into diagnostic output'
fi
grep -Fq 'Ignoring line 1' "$TMP_DIR/validator.out" || fail 'malformed line lost its line number'
grep -Fq 'MODE: invalid value' "$TMP_DIR/validator.out" || fail 'enum error lost its key'
grep -Fq 'COUNT: expected integer' "$TMP_DIR/validator.out" || fail 'type error lost its key'
pass 'validator errors retain keys and line numbers without raw values'

source "$ROOT_DIR/installers/lib/secure-log.sh"
mode() {
    local value
    value="$(_ods_install_log_mode "$1")"
    printf '%o' "$((8#$value & 07777))"
}

LOG_FILE="$TMP_DIR/help.log" bash "$ROOT_DIR/install.sh" --help \
    >"$TMP_DIR/help.out" 2>&1 || fail '--help failed'
[[ ! -e "$TMP_DIR/help.log" ]] || fail '--help created an installer log'
if LOG_FILE="$TMP_DIR/unknown.log" bash "$ROOT_DIR/install.sh" --not-an-option \
    >"$TMP_DIR/unknown.out" 2>&1; then
    fail 'unknown option was accepted'
fi
[[ ! -e "$TMP_DIR/unknown.log" ]] || fail 'unknown option created an installer log'
pass 'help and option errors do not create diagnostic files'

( umask 0002; ods_prepare_install_log "$TMP_DIR/fresh.log" ) || fail 'could not create fresh log'
[[ "$(mode "$TMP_DIR/fresh.log")" == 600 ]] || fail 'fresh log is not private'
pass 'fresh log is 0600 despite caller umask 0002'

printf 'preserve this diagnostic\n' >"$TMP_DIR/existing.log"
chmod 0644 "$TMP_DIR/existing.log"
ods_prepare_install_log "$TMP_DIR/existing.log" || fail 'could not secure existing log'
[[ "$(mode "$TMP_DIR/existing.log")" == 600 ]] || fail 'existing log is not private'
[[ "$(cat "$TMP_DIR/existing.log")" == 'preserve this diagnostic' ]] || fail 'existing log was truncated'
pass 'existing owned regular log is privatized without truncation'

printf 'do not follow\n' >"$TMP_DIR/target.txt"
ln -s "$TMP_DIR/target.txt" "$TMP_DIR/link.log"
if ods_prepare_install_log "$TMP_DIR/link.log" 2>/dev/null; then
    fail 'symlink log was accepted'
fi
[[ "$(cat "$TMP_DIR/target.txt")" == 'do not follow' ]] || fail 'symlink target was changed'
pass 'symlink log is rejected'

mkdir "$TMP_DIR/unsafe-parent"
chmod 0777 "$TMP_DIR/unsafe-parent"
if ods_prepare_install_log "$TMP_DIR/unsafe-parent/new.log" 2>/dev/null; then
    fail 'non-sticky world-writable log directory was accepted'
fi
pass 'unsafe log parent is rejected'

ods_prepare_install_log /dev/null || fail '/dev/null diagnostic sink was rejected'
pass '/dev/null remains a supported explicit diagnostic sink'

mkfifo "$TMP_DIR/fifo.log"
python3 - "$ROOT_DIR/installers/lib/secure-log.sh" "$TMP_DIR/fifo.log" <<'PY'
import subprocess
import sys
try:
    result = subprocess.run(['bash', '-c', 'source "$1"; ods_prepare_install_log "$2"',
                             'fifo-test', *sys.argv[1:]], timeout=3, capture_output=True)
except subprocess.TimeoutExpired:
    raise SystemExit('FIFO guard blocked while opening the pipe')
if result.returncode == 0:
    raise SystemExit('FIFO log was accepted')
PY
pass 'FIFO is rejected without blocking or opening it'
if ods_prepare_install_log /dev/zero 2>/dev/null; then fail 'device log was accepted'; fi
pass 'device log is rejected; /dev/null is the only explicit sink'

printf 'hardlink sentinel\n' > "$TMP_DIR/hardlink-target"
ln "$TMP_DIR/hardlink-target" "$TMP_DIR/hardlink.log"
chmod 0644 "$TMP_DIR/hardlink-target"
if ods_prepare_install_log "$TMP_DIR/hardlink.log" 2>/dev/null; then fail 'hardlink log accepted'; fi
[[ "$(mode "$TMP_DIR/hardlink-target")" == 644 ]] || fail 'hardlink target permissions changed'
[[ "$(cat "$TMP_DIR/hardlink-target")" == 'hardlink sentinel' ]] || fail 'hardlink target changed'
pass 'multiply linked log is rejected without changing its other name'

mkdir "$TMP_DIR/alias-first" "$TMP_DIR/alias-second"
ln -s "$TMP_DIR/alias-first" "$TMP_DIR/parent-alias"
override_log="$TMP_DIR/parent-alias/override.log"
ods_prepare_install_log_var override_log || fail 'safe parent alias was rejected'
[[ "$override_log" == "$TMP_DIR/alias-first/override.log" ]] || fail 'caller kept mutable parent alias'
rm "$TMP_DIR/parent-alias"
ln -s "$TMP_DIR/alias-second" "$TMP_DIR/parent-alias"
printf 'reopened canonical path\n' >> "$override_log"
[[ ! -e "$TMP_DIR/alias-second/override.log" ]] || fail 'reopen followed replaced alias'
grep -Fq 'reopened canonical path' "$TMP_DIR/alias-first/override.log" || fail 'canonical write missing'
pass 'parent alias replacement after preparation cannot redirect caller writes'

mkdir "$TMP_DIR/shared"
chmod 1777 "$TMP_DIR/shared"
if ods_prepare_install_log "$TMP_DIR/shared/predictable.log" 2>/dev/null; then
    fail 'predictable override in a shared sticky directory was accepted'
fi
[[ ! -e "$TMP_DIR/shared/predictable.log" ]] || fail 'shared override created a file'
default_log=""
TMPDIR="$TMP_DIR/shared" ods_prepare_install_log_var default_log || fail 'private default allocation failed'
[[ "$(mode "$(dirname "$default_log")")" == 700 ]] || fail 'default directory not private'
[[ "$(mode "$default_log")" == 600 ]] || fail 'default log not private'
second_default=""
TMPDIR="$TMP_DIR/shared" ods_prepare_install_log_var second_default || fail 'second default failed'
[[ "$default_log" != "$second_default" ]] || fail 'independent runs reused a predictable default'
pass 'defaults use separate 0700 directories; shared-path overrides fail before creation'

mkdir -p "$TMP_DIR/unsafe-ancestor/nested"
chmod 0777 "$TMP_DIR/unsafe-ancestor"
if ods_prepare_install_log "$TMP_DIR/unsafe-ancestor/nested/log" 2>/dev/null; then
    fail 'replaceable ancestor bypassed the immediate parent check'
fi
pass 'a secure leaf directory does not hide a replaceable ancestor'

legacy="$TMP_DIR/shared/ods-install.log"
printf 'keep legacy history\n' > "$legacy"
chmod 0644 "$legacy"
new_log=""
TMPDIR="$TMP_DIR/shared" ods_prepare_install_log_var new_log "$legacy" || fail 'legacy retirement failed'
[[ "$(mode "$legacy")" == 600 ]] || fail 'owned legacy log remains readable by others'
[[ "$(cat "$legacy")" == 'keep legacy history' ]] || fail 'legacy history was removed or truncated'
[[ "$new_log" != "$legacy" ]] || fail 'new run reused old shared filename'
ln -s "$TMP_DIR/target.txt" "$TMP_DIR/shared/legacy-link.log"
new_log=""
TMPDIR="$TMP_DIR/shared" ods_prepare_install_log_var new_log "$TMP_DIR/shared/legacy-link.log" 2>/dev/null \
    || fail 'unsafe legacy file prevented a separate safe default'
[[ "$(cat "$TMP_DIR/target.txt")" == 'do not follow' ]] || fail 'legacy cleanup followed a symlink'
pass 'legacy owned logs become private without deletion; legacy symlinks stay untouched'

# Exercise the real build-log boundary without Docker, a build, or an install.
eval "$(sed -n '/^_phase11_build_local_images() {/,/^}$/p' "$ROOT_DIR/installers/phases/11-services.sh")"
LOG_FILE="$TMP_DIR/main.log"
ods_prepare_install_log_var LOG_FILE
ln -s "$TMP_DIR/target.txt" "$LOG_FILE.comfyui.build.log"
DOCKER_COMPOSE_CMD=false
if _phase11_build_local_images comfyui 2>/dev/null; then fail 'derived build log followed a link'; fi
[[ "$(cat "$TMP_DIR/target.txt")" == 'do not follow' ]] || fail 'build guard truncated symlink target'
pass 'actual derived build helper rejects symlink before truncate or Docker execution'

eval "$(sed -n '/^_macos_launch_detached_bootstrap_upgrade() {/,/^}$/p' "$ROOT_DIR/installers/macos/install-macos.sh")"
INSTALL_DIR="$TMP_DIR/macos-fixture"
mkdir -p "$INSTALL_DIR/logs"
ln -s "$TMP_DIR/target.txt" "$INSTALL_DIR/logs/model-upgrade.log"
if _macos_launch_detached_bootstrap_upgrade missing-fixture "$INSTALL_DIR" 2>/dev/null; then
    fail 'macOS detached helper accepted symlink log'
fi
[[ "$(cat "$TMP_DIR/target.txt")" == 'do not follow' ]] || fail 'macOS helper changed symlink target'
pass 'macOS detached helper rejects symlink before Python opens a log or starts a child'

# Ownership and hostile-UID replacement use only disposable fixtures. Skip on
# hosts without passwordless sudo; do not weaken the checks to make tests pass.
if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    printf 'foreign owner sentinel\n' > "$TMP_DIR/foreign.log"
    chmod 0644 "$TMP_DIR/foreign.log"
    sudo -n chown 65534 "$TMP_DIR/foreign.log"
    if ods_prepare_install_log "$TMP_DIR/foreign.log" 2>/dev/null; then fail 'foreign-owned log accepted'; fi
    [[ "$(mode "$TMP_DIR/foreign.log")" == 644 ]] || fail 'foreign file permissions changed'
    sudo -n chown "$EUID" "$TMP_DIR/foreign.log"
    pass 'foreign-owned regular log is rejected without chmod'

    mkdir -p "$TMP_DIR/foreign-parent/owned-child"
    chmod 0755 "$TMP_DIR/foreign-parent"
    sudo -n chown 65534 "$TMP_DIR/foreign-parent"
    if ods_prepare_install_log "$TMP_DIR/foreign-parent/owned-child/log" 2>/dev/null; then
        fail 'foreign-owned ancestor accepted above an owned child'
    fi
    [[ ! -e "$TMP_DIR/foreign-parent/owned-child/log" ]] || fail 'foreign ancestor check occurred after write'
    sudo -n chown "$EUID" "$TMP_DIR/foreign-parent"
    pass 'foreign-owned ancestor is rejected even when its current mode is not writable'

    chmod 0755 "$TMP_DIR"
    sudo -n python3 - "$default_log" "$TMP_DIR/target.txt" <<'PY'
import os
from pathlib import Path
import sys
log = Path(sys.argv[1])
target = Path(sys.argv[2])
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
for operation in (lambda: log.unlink(), lambda: log.symlink_to(target),
                  lambda: log.parent.rename(log.parent.with_name(log.parent.name + '.replaced'))):
    try:
        operation()
    except PermissionError:
        continue
    raise SystemExit('another UID replaced a prepared private log/path')
PY
    printf 'write after hostile UID attempts\n' >> "$default_log"
    grep -Fq 'write after hostile UID attempts' "$default_log" || fail 'protected reopen failed'
    pass 'another UID cannot replace prepared file or its private parent before reopen'
else
    printf '[SKIP] three foreign-owner/ancestor and hostile-UID fixtures require passwordless sudo\n'
fi
