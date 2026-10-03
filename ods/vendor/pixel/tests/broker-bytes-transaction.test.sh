#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SOURCE/scripts/lib/common.sh"
# shellcheck source=scripts/lib/broker-bytes.sh
source "$SOURCE/scripts/lib/broker-bytes.sh"

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
fake_systemctl="$tmp/systemctl"
cat > "$fake_systemctl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${PIXEL_FAKE_SYSTEMCTL_LOG:?}"
if [[ ${1:-} == show ]]; then printf 'success\n'; fi
FAKE
chmod 700 "$fake_systemctl"

export PIXEL_BROKER_BYTES_TESTING=1
export PIXEL_BROKER_BYTES_TEST_ROOT="$tmp"
export PIXEL_BROKER_BYTES_SYSTEMCTL_BIN="$fake_systemctl"
export PIXEL_FAKE_SYSTEMCTL_LOG="$tmp/systemctl.log"
export PIXEL_SOURCE_BROKER_ENABLED=1
export PIXEL_SOURCE_BROKER_INSTALL_DIR="$tmp/install/source"
export PIXEL_SOURCE_BROKER_UNIT=pixel-source-broker.service
export PIXEL_SOURCE_BROKER_TIMER=pixel-source-broker.timer
export PIXEL_CALENDAR_DIRECT_ENABLED=0
export PIXEL_OPS_BROKER_ENABLED=0
export PIXEL_OPS_BROKER_INSTALL_DIR="$tmp/install/ops"
export PIXEL_OPS_BROKER_UNIT=pixel-ops-broker.service
export PIXEL_FRONTIER_BROKER_ENABLED=1
export PIXEL_FRONTIER_BROKER_INSTALL_DIR="$tmp/install/frontier"
export PIXEL_FRONTIER_BROKER_UNIT=pixel-frontier-broker.service

# Prestate: the source runtime is a single broker.py byte only. The 4.3.9 repair bundles the
# action-journal and vendor-secret helpers inside broker.py, so prestate carries neither the
# action_journal directory nor vendor_secrets.py (matching the exact old-controller manifest).
mkdir -p "$PIXEL_SOURCE_BROKER_INSTALL_DIR" "$PIXEL_OPS_BROKER_INSTALL_DIR" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR"
chmod 755 "$PIXEL_SOURCE_BROKER_INSTALL_DIR" "$PIXEL_OPS_BROKER_INSTALL_DIR" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR"
printf 'old source\n' > "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py"
printf 'old ops\n' > "$PIXEL_OPS_BROKER_INSTALL_DIR/broker.py"
printf 'old frontier\n' > "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/broker.py"
chmod 755 "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py" "$PIXEL_OPS_BROKER_INSTALL_DIR/broker.py" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/broker.py"
# verify-codex.py is deliberately absent: apply must create it and rollback must remove it.

assert_source_prestate() {
  [[ -f "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py" && ! -L "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py" ]] \
    || { echo "source broker.py is not a single regular file" >&2; exit 1; }
  # Full installed path set equals prestate: no stray helper file or directory.
  local stray
  stray=$(find "$PIXEL_SOURCE_BROKER_INSTALL_DIR" -mindepth 1 ! -name 'broker.py' ! -path '*/__pycache__*' -print -quit)
  [[ -z "$stray" ]] || { echo "stray helper path present: $stray" >&2; exit 1; }
}

release="$tmp/release"
mkdir -p "$release/source-broker" "$release/github-broker" "$release/ops-broker" "$release/frontier-broker"
printf 'new source\n' > "$release/source-broker/broker.py"
printf 'new github\n' > "$release/github-broker/broker.py"
printf 'new ops\n' > "$release/ops-broker/broker.py"
printf 'new frontier\n' > "$release/frontier-broker/broker.py"
printf 'new verifier\n' > "$release/frontier-broker/verify-codex.py"
chmod 700 "$release/source-broker/broker.py" "$release/github-broker/broker.py" "$release/ops-broker/broker.py" "$release/frontier-broker/broker.py" "$release/frontier-broker/verify-codex.py"

backup="$tmp/backup"
pixel_broker_bytes_backup_all "$backup"
# New-format source backup preserves the old controller's exact manifest rows: broker.py
# present and the action_journal package absent, with no vendor_secrets row.
grep -Fq 'broker.py|present|' "$backup/brokers/source/manifest.tsv"
grep -Fqx 'action_journal/__init__.py|absent|-|0644' "$backup/brokers/source/manifest.tsv"
! grep -Fq 'vendor_secrets.py' "$backup/brokers/source/manifest.tsv" || { echo "manifest retained a vendor_secrets row" >&2; exit 1; }
grep -Fqx 'verify-codex.py|absent|-|0755' "$backup/brokers/frontier/manifest.tsv"
[[ ! -e "$backup/brokers/ops" ]] || { echo "disabled ops limb was backed up" >&2; exit 1; }

pixel_broker_bytes_install_all "$release" "$backup"
pixel_broker_bytes_verify "$release"
cmp -s "$release/source-broker/broker.py" "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py"
[[ ! -e "$PIXEL_SOURCE_BROKER_INSTALL_DIR/action_journal" ]] || { echo "install created an action_journal directory" >&2; exit 1; }
[[ ! -e "$PIXEL_SOURCE_BROKER_INSTALL_DIR/vendor_secrets.py" ]] || { echo "install created a vendor_secrets module" >&2; exit 1; }
cmp -s "$release/frontier-broker/verify-codex.py" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/verify-codex.py"
[[ "$(cat "$PIXEL_OPS_BROKER_INSTALL_DIR/broker.py")" == 'old ops' ]]
grep -Fq 'stop pixel-source-broker.timer' "$PIXEL_FAKE_SYSTEMCTL_LOG"
grep -Fq 'restart pixel-frontier-broker.service' "$PIXEL_FAKE_SYSTEMCTL_LOG"

printf 'drift\n' >> "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py"
if (pixel_broker_bytes_verify "$release") >/dev/null 2>&1; then
  echo "broker verification accepted installed-byte drift" >&2
  exit 1
fi

# --- Automatic compensation: the current controller restores the new-format activation backup. ---
pixel_broker_bytes_restore_all "$backup"
[[ "$(cat "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py")" == 'old source' ]]
[[ "$(cat "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/broker.py")" == 'old frontier' ]]
[[ ! -e "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/verify-codex.py" ]] || { echo "rollback retained a candidate-created file" >&2; exit 1; }
assert_source_prestate

# --- Explicit rollback through the exact old trusted controller -------------------------------
# Reproduce the reported 4.3.3->4.3.8 failure: a new-format activation backup restored by the
# old controller. With the 4.3.9 bundled runtime the source manifest has exactly the two legacy
# rows, so the old controller must succeed instead of dying on "unexpected records".
rollback_backup="$tmp/rollback-backup"
pixel_broker_bytes_backup_all "$rollback_backup"
pixel_broker_bytes_install_all "$release" "$rollback_backup"
[[ "$(cat "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py")" == 'new source' ]]
# shellcheck source=tests/fixtures/old-broker-bytes-709939b.sh
source "$SOURCE/tests/fixtures/old-broker-bytes-709939b.sh"
pixel_broker_bytes_restore_all "$rollback_backup"
[[ "$(cat "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py")" == 'old source' ]]
assert_source_prestate

# Restore the current controller's broker-byte library for the remaining negative coverage.
# shellcheck source=scripts/lib/broker-bytes.sh
source "$SOURCE/scripts/lib/broker-bytes.sh"

# --- Negative: linked destination must not be followed. ----------------------------------------
outside="$tmp/outside"
printf 'do not overwrite\n' > "$outside"
ln -s "$outside" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/verify-codex.py"
if (pixel_broker_bytes_install_all "$release" "$backup") >/dev/null 2>&1; then
  echo "broker transaction accepted a linked destination" >&2
  exit 1
fi
[[ "$(cat "$outside")" == 'do not overwrite' ]] || { echo "linked destination was followed" >&2; exit 1; }
rm -f -- "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/verify-codex.py"

# --- Negative: tampered backup must be rejected. ----------------------------------------------
cp -a "$backup" "$tmp/tampered-backup"
printf 'tampered\n' > "$tmp/tampered-backup/brokers/source/broker.py"
if (pixel_broker_bytes_restore_all "$tmp/tampered-backup") >/dev/null 2>&1; then
  echo "rollback accepted a changed broker backup" >&2
  exit 1
fi

export PIXEL_RELEASE_OPERATOR_ENABLED=1
unset PIXEL_BROKER_BYTES_DIRECT_SUDO
operator_log="$tmp/release-operator.log"
pixel_release_operator_client() { printf '%s\n' "$*" >> "$operator_log"; }
pixel_broker_bytes_require_trust
pixel_broker_bytes_backup_all "$tmp/caller-backup-must-not-cross-boundary"
pixel_broker_bytes_install_all "$tmp/caller-release-must-not-cross-boundary" "$tmp/caller-backup-must-not-cross-boundary"
pixel_broker_bytes_verify "$tmp/caller-release-must-not-cross-boundary"
pixel_broker_bytes_restore_all "$tmp/caller-backup-must-not-cross-boundary"
cat > "$tmp/expected-operator.log" <<'EXPECTED'
--validate-config
broker-bytes backup
broker-bytes install
broker-bytes verify
broker-bytes restore
EXPECTED
cmp -s "$tmp/expected-operator.log" "$operator_log" || {
  echo "release-operator bridge forwarded authority or changed its fixed verb sequence" >&2
  exit 1
}
export PIXEL_BROKER_BYTES_DIRECT_SUDO=1
pixel_broker_bytes_require_trust

chmod 775 "$PIXEL_SOURCE_BROKER_INSTALL_DIR"
if (pixel_broker_bytes_require_trust) >/dev/null 2>&1; then
  echo "broker-byte trust accepted a group-writable install root" >&2
  exit 1
fi
chmod 755 "$PIXEL_SOURCE_BROKER_INSTALL_DIR"

export PIXEL_SOURCE_BROKER_INSTALL_DIR=/opt/pixel-source-broker
if (pixel_broker_bytes_require_trust) >/dev/null 2>&1; then
  echo "broker-byte test mode accepted a production install path" >&2
  exit 1
fi

export PIXEL_SOURCE_BROKER_INSTALL_DIR="$tmp/install/source"
mv "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py" "$tmp/source-broker-real"
ln -s "$tmp/source-broker-real" "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py"
if (pixel_broker_bytes_require_trust) >/dev/null 2>&1; then
  echo "broker-byte discovery treated a linked primary executable as uninstalled" >&2
  exit 1
fi

echo "broker-byte transaction regression passed"
