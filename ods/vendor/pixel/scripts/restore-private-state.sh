#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
# shellcheck source=scripts/lib/broker-reader-acls.sh
source "$ROOT/scripts/lib/broker-reader-acls.sh"
MIGRATION_CUSTODY='/var/lib/pixel-migration-journals'

usage() {
  echo "Usage: ./pixel restore BACKUP.tar.gz.age --identity AGE_IDENTITY [--signers ALLOWED_SIGNERS] [--validate-only | --rehearse /new/empty/root | --replace --confirm [--receipt ABSOLUTE_NEW_PATH]] [--migration JOURNAL [--migration-spec SPEC --migration-stage STAGE]] [--knowledge-vault-id ID --restored-knowledge-key PATH --current-knowledge-key PATH]" >&2
  echo "       ./pixel restore --migration-commit|--migration-rollback JOURNAL" >&2
  exit 2
}

# --------------------------------------------------------------------------- #
# Migration-only two-phase control verbs. Only the clean-migration activate
# transaction reaches these; a standard restore is fully self-contained and never
# does. They finalize (commit) or undo (rollback) an already-armed migration swap
# described by the root-owned, mode-0600 transaction journal the migration swap
# wrote under the authenticated 3.2 root contract. The control verbs re-validate the
# journal schema, ownership/custody, source/target/backup identity, destination
# membership in the recorded trusted contract, exact derived transaction sibling
# names, and the fixed Pixel service contract before performing any privileged
# operation. Commit marks the state committed (durably and atomically) BEFORE any
# destructive cleanup and is idempotent, so a cleanup failure retains the verified
# new live state and never triggers rollback. Standard restore never widens its
# allowlist and never reads or writes a migration journal.
# --------------------------------------------------------------------------- #
if [[ ${1:-} == --migration-commit || ${1:-} == --migration-rollback ]]; then
  control_mode=${1#--migration-}
  shift
  [[ $# -eq 1 ]] || usage
  journal=$1
  # Migration journals live in a fixed, root-owned, mode-0700 custody directory and must
  # be directly beneath it with a safe basename. Ownership trust is derived ONLY from the
  # actual privileged euid (root in production) by the single privileged helper below; there
  # is no caller- or environment-supplied owner override anywhere in the production path. All
  # exact-key validation, locking, per-root progress, commit, rollback, and finalization are
  # owned by scripts/restore-migration-journal.py, so the shell contains no embedded duplicate
  # parser. The helper runs under the same privileged context it uses for the swap so it can
  # read the root-owned transaction journal; all destructive and service operations still go
  # through sudo/trusted systemctl so the test harness can substitute fakes.
  [[ "$journal" == "$MIGRATION_CUSTODY"/* && "$journal" != "$MIGRATION_CUSTODY" ]] || pixel_die "Migration journal must be inside the custody directory"
  [[ "$(dirname "$journal")" == "$MIGRATION_CUSTODY" ]] || pixel_die "Migration journal must be directly beneath the custody directory"
  [[ "$(basename "$journal")" =~ ^[A-Za-z0-9._-]+$ ]] || pixel_die "Migration journal basename is unsafe"
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" "$control_mode"     "$MIGRATION_CUSTODY" "$journal"
  exit 0
fi

[[ $# -ge 3 ]] || usage
backup=$1; shift
identity=''; signers=${PIXEL_BACKUP_ALLOWED_SIGNERS:-${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment/backup-allowed-signers}; mode=restore; rehearsal=''; replace=0; confirm=0; receipt=''; receipt_reserved=0; receipt_finalized=0; source_pixel=''; target_pixel=''; migration_journal=''; migration_spec=''; migration_stage=''
knowledge_vault_id=${PIXEL_KNOWLEDGE_VAULT_ID:-}; restored_knowledge_key=''; current_knowledge_key=${PIXEL_KNOWLEDGE_VAULT_CREDENTIAL:-/etc/pixel-work-credentials/pixel-knowledge-vault-key}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --identity) [[ $# -ge 2 ]] || usage; identity=$2; shift 2 ;;
    --signers) [[ $# -ge 2 ]] || usage; signers=$2; shift 2 ;;
    --validate-only) mode=validate; shift ;;
    --rehearse) [[ $# -ge 2 ]] || usage; mode=rehearse; rehearsal=$2; shift 2 ;;
    --replace) replace=1; shift ;;
    --confirm) confirm=1; shift ;;
    --receipt) [[ $# -ge 2 ]] || usage; receipt=$2; shift 2 ;;
    --migration) [[ $# -ge 2 ]] || usage; migration_journal=$2; shift 2 ;;
    --migration-spec) [[ $# -ge 2 ]] || usage; migration_spec=$2; shift 2 ;;
    --migration-stage) [[ $# -ge 2 ]] || usage; migration_stage=$2; shift 2 ;;
    --knowledge-vault-id) [[ $# -ge 2 ]] || usage; knowledge_vault_id=$2; shift 2 ;;
    --restored-knowledge-key) [[ $# -ge 2 ]] || usage; restored_knowledge_key=$2; shift 2 ;;
    --current-knowledge-key) [[ $# -ge 2 ]] || usage; current_knowledge_key=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$identity" ]] || usage
for command in age python3 sha256sum ssh-keygen tar realpath mktemp; do pixel_require_command "$command"; done
[[ "$backup" == /* && -f "$backup" && ! -L "$backup" ]] || pixel_die "Backup must be an absolute regular non-symlink file"
[[ -f "$backup.sha256" && ! -L "$backup.sha256" ]] || pixel_die "Backup checksum file is missing or unsafe"
[[ -f "$backup.sig" && ! -L "$backup.sig" ]] || pixel_die "Backup signature file is missing or unsafe"
[[ "$identity" == /* && -f "$identity" && ! -L "$identity" ]] || pixel_die "Age identity must be an absolute regular non-symlink file"
[[ "$signers" == /* && -f "$signers" && ! -L "$signers" ]] || pixel_die "Backup allowed-signers file must be an absolute regular non-symlink file"
identity_mode=$(stat -c %a "$identity")
(( (8#$identity_mode & 8#077) == 0 )) || pixel_die "Age identity must not be group/world accessible"
[[ $mode != restore || $confirm == 1 ]] || pixel_die "Restoring private state requires --confirm"
[[ $mode == restore || $replace == 0 ]] || pixel_die "--replace is valid only for an actual restore"
[[ $mode == restore || -z "$receipt" ]] || pixel_die "--receipt is valid only for an actual confirmed restore"
[[ -z "$migration_journal" || ( $mode == restore && -n "$receipt" ) ]] || pixel_die "--migration is valid only for an actual confirmed restore with --receipt"
if [[ -n "$migration_spec" || -n "$migration_stage" ]]; then
  [[ -n "$migration_journal" && -n "$migration_spec" && -n "$migration_stage" ]]     || pixel_die "--migration-spec and --migration-stage must be supplied together and only with --migration"
  [[ "$migration_spec" == /* && -f "$migration_spec" && ! -L "$migration_spec" ]]     || pixel_die "Migration deployment spec must be an absolute regular non-symlink file"
  [[ "$migration_stage" == /* && -d "$migration_stage" ]] || pixel_die "Migration stage must be an absolute existing directory"
fi
case ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} in 0|1) ;; *) pixel_die "PIXEL_DEEP_WORK_BACKUP_ENABLED must be 0 or 1" ;; esac
# Initialize EVERY cleanup variable and install the EXIT trap BEFORE any reservation so that
# an early checksum/lock/validation failure still removes the exact reservation and the trap
# never references an unbound variable under `set -u` (reservation_active is 0 when no
# --migration is supplied, and reservation_token/journal_armed default to their safe values).
frozen_dir=''; allowed_file=''; manifest_file=''; stage=''; knowledge_restore_stage=''; defer_rollback=0
receipt_reserved=0; receipt_finalized=0; reservation_active=0; reservation_token=''; journal_armed=0; expected=''
live_mutation_started=0; retain_reservation=0; temp_copies_created=0
cleanup() {
  if [[ $receipt_reserved == 1 && $receipt_finalized == 0 ]]; then
    python3 "$ROOT/scripts/restore-receipt.py" abort "$receipt" "$ROOT" >/dev/null 2>&1 || true
  fi
  if [[ $reservation_active == 1 && $journal_armed == 0 && $retain_reservation == 0 && -n "$migration_journal" && -n "$reservation_token" ]]; then
    sudo python3 "$ROOT/scripts/restore-migration-journal.py" abort       "$MIGRATION_CUSTODY" "$migration_journal" "$reservation_token"       "$PIXEL_MIGRATION_CONTRACT_SHA256" "$expected" >/dev/null 2>&1 || true
  fi
  [[ -z "$allowed_file" ]] || rm -f -- "$allowed_file"
  if [[ -n "$manifest_file" ]]; then rm -f -- "$manifest_file" "$manifest_file.recheck"; fi
  [[ -z "$frozen_dir" ]] || rm -rf -- "$frozen_dir"
  if [[ -n "$stage" && "$stage" == /var/tmp/pixel-restore.* ]]; then sudo rm -rf -- "$stage"; fi
  if [[ -n "$knowledge_restore_stage" && "$knowledge_restore_stage" == /var/tmp/pixel-knowledge-restore.* ]]; then sudo rm -rf -- "$knowledge_restore_stage"; fi
}
trap cleanup EXIT
# Parse and validate the exact backup checksum BEFORE the migration reservation so the
# reserved marker is bound to the exact backup identity, and so a malformed checksum fails
# before any reservation or live mutation.
checksum_lines=$(wc -l < "$backup.sha256")
expected=$(awk 'NR == 1 {print $1}' "$backup.sha256")
[[ $checksum_lines == 1 && "$expected" =~ ^[a-f0-9]{64}$ ]] || pixel_die "Backup checksum file is malformed"
if [[ -n "$migration_journal" ]]; then
  [[ -n ${PIXEL_MIGRATION_CONTRACT_SHA256:-} ]] || pixel_die "--migration requires PIXEL_MIGRATION_CONTRACT_SHA256 (the exact 3.2 root-contract evidence)"
  [[ "$PIXEL_MIGRATION_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ ]] || pixel_die "PIXEL_MIGRATION_CONTRACT_SHA256 must be a 64-character lowercase hex digest"
  # Migration-only reservation BEFORE any live mutation. The non-root orchestrator cannot
  # observe or write inside the root-owned mode-0700 custody directory, so it validates only
  # the literal fixed path/basename and delegates custody setup and the exact O_EXCL
  # reservation to the privileged helper, which is the single authoritative owner of the
  # reservation lifecycle. The reservation is O_EXCL-only: EVERY pre-existing marker
  # (including a stale reservation for this same contract) is refused; there is no implicit
  # stale reclaim in normal activation. The per-invocation cryptographic reservation token
  # printed on stdout is captured here and required by both arm and abort, so a concurrent
  # same-contract activation can never reclaim or arm this invocation's reservation.
  [[ "$migration_journal" == "$MIGRATION_CUSTODY"/* && "$migration_journal" != "$MIGRATION_CUSTODY" ]] || pixel_die "Migration journal must be inside the custody directory"
  [[ "$(dirname "$migration_journal")" == "$MIGRATION_CUSTODY" ]] || pixel_die "Migration journal must be directly beneath the custody directory"
  [[ "$(basename "$migration_journal")" =~ ^[A-Za-z0-9._-]+$ ]] || pixel_die "Migration journal basename is unsafe"
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" ensure "$MIGRATION_CUSTODY"     || pixel_die "Migration custody directory could not be created/validated; nothing was changed"
  reservation_token=$(sudo python3 "$ROOT/scripts/restore-migration-journal.py" reserve     "$MIGRATION_CUSTODY" "$migration_journal" "$PIXEL_MIGRATION_CONTRACT_SHA256" "$expected")     || pixel_die "Migration journal path is occupied, reserved for a different contract/backup, or unsafe; nothing was changed"
  reservation_active=1
fi
# Preflight and atomically reserve the exact new receipt path before any live-state
# transaction, so an invalid/unwritable/occupied/unsafe receipt path fails before mutation.
if [[ $mode == restore && -n "$receipt" ]]; then
  python3 "$ROOT/scripts/restore-receipt.py" reserve "$receipt" "$ROOT" || pixel_die "Restore receipt path is invalid, unwritable, unsafe, or already occupied; nothing was changed"
  receipt_reserved=1
fi
if [[ $mode == restore ]]; then pixel_acquire_deployment_lock exclusive; fi

frozen_dir=$(mktemp -d)
chmod 700 "$frozen_dir"
frozen_backup="$frozen_dir/backup.tar.gz.age"
frozen_signature="$frozen_dir/backup.tar.gz.age.sig"
frozen_identity="$frozen_dir/identity"
frozen_signers="$frozen_dir/allowed-signers"
cp -- "$backup" "$frozen_backup"
cp -- "$backup.sig" "$frozen_signature"
cp -- "$identity" "$frozen_identity"
cp -- "$signers" "$frozen_signers"
chmod 400 "$frozen_backup" "$frozen_signature" "$frozen_identity" "$frozen_signers"
observed=$(sha256sum "$frozen_backup" | awk '{print $1}')
[[ "$observed" == "$expected" ]] || pixel_die "Encrypted backup checksum does not match"
python3 - "$frozen_signature" <<'PY'
import base64, pathlib, re, sys
raw = pathlib.Path(sys.argv[1]).read_bytes()
if not raw or len(raw) > 16384 or not raw.endswith(b"\n"):
    raise SystemExit("backup signature envelope is malformed")
try:
    lines = raw.decode("ascii").splitlines()
except UnicodeDecodeError as error:
    raise SystemExit("backup signature envelope is not ASCII") from error
if (
    len(lines) < 3 or lines[0] != "-----BEGIN SSH SIGNATURE-----"
    or lines[-1] != "-----END SSH SIGNATURE-----"
    or lines.count(lines[0]) != 1 or lines.count(lines[-1]) != 1
    or any(not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", line) for line in lines[1:-1])
):
    raise SystemExit("backup signature envelope is non-canonical")
base64.b64decode("".join(lines[1:-1]), validate=True)
PY
ssh-keygen -Y verify -f "$frozen_signers" -I pixel-backup -n pixel-private-backup -s "$frozen_signature" < "$frozen_backup" >/dev/null 2>&1 || pixel_die "Encrypted backup signature is not trusted"

allowed=()
deep_path() {
  local value=$1 label=$2 normalized
  [[ "$value" == /* && "$value" != / && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || pixel_die "$label must be an absolute non-root path"
  normalized=$(realpath -m "$value")
  [[ "$normalized" == "$value" ]] || pixel_die "$label must be normalized"
  printf '%s' "$normalized"
}
path_contains() { [[ $2 == "$1" || $2 == "$1/"* ]]; }
add_allowed() {
  local path=$1
  [[ "$path" == /* && "$path" != / && "$path" != *$'\n'* && "$path" != *$'\r'* ]] || pixel_die "Unsafe private-state restore path"
  allowed+=("${path#/}")
}
add_allowed "$OPENCLAW_HOME/openclaw.json"
add_allowed "$PIXEL_WORKSPACE"
add_allowed "$PIXEL_GOOGLE_TOKEN_PATH"
add_allowed "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent/gateway.env"
add_allowed "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment"
add_allowed "${PIXEL_CONTROL_POLICY_PATH:-${XDG_CONFIG_HOME:-$HOME/.config}/pixel-control/policy.json}"
[[ -z ${PIXEL_PRIVATE_ONBOARDING_PATH:-} ]] || add_allowed "$PIXEL_PRIVATE_ONBOARDING_PATH"
add_allowed "$PIXEL_ROOT/.env"
add_allowed "${PIXEL_SOURCE_BROKER_STATE_DIR:-/var/lib/pixel-source-broker}"
add_allowed "${PIXEL_SOURCE_BROKER_ENV:-/etc/pixel-source-broker.env}"
add_allowed "${PIXEL_OPS_BROKER_STATE_DIR:-/var/lib/pixel-ops-broker}"
add_allowed "${PIXEL_OPS_BROKER_ENV:-/etc/pixel-ops-broker.env}"
add_allowed "$(dirname "${PIXEL_OPS_POLICY_PATH:-/etc/pixel-ops-broker/policy.json}")"
add_allowed "${PIXEL_FRONTIER_BROKER_STATE_DIR:-/var/lib/pixel-frontier-broker}"
add_allowed "${PIXEL_FRONTIER_BROKER_ENV:-/etc/pixel-frontier-broker.env}"
add_allowed "$(dirname "${PIXEL_FRONTIER_POLICY_PATH:-/etc/pixel-frontier-broker/policy.json}")"
knowledge_relative=''
if [[ ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} == 1 ]]; then
  work_state_root=$(deep_path "${PIXEL_WORK_STATE_ROOT:-/var/lib/pixel-work}" "Deep Work state root")
  work_config_root=$(deep_path "${PIXEL_WORK_CONFIG_ROOT:-/etc/pixel-work}" "Deep Work configuration root")
  knowledge_vault_root=$(deep_path "${PIXEL_KNOWLEDGE_VAULT_ROOT:-/var/lib/pixel-knowledge/vault}" "knowledge vault root")
  current_knowledge_key=$(deep_path "$current_knowledge_key" "current knowledge vault key")
  deep_roots=("$work_state_root" "$work_config_root" "$knowledge_vault_root")
  for ((left=0; left<${#deep_roots[@]}; left++)); do
    path_contains "${deep_roots[$left]}" "$current_knowledge_key" && pixel_die "The external knowledge-vault credential must be outside every captured restore root"
    for ((right=left+1; right<${#deep_roots[@]}; right++)); do
      if path_contains "${deep_roots[$left]}" "${deep_roots[$right]}" || path_contains "${deep_roots[$right]}" "${deep_roots[$left]}"; then pixel_die "Deep Work restore roots must be separate and non-nested"; fi
    done
  done
  add_allowed "$work_state_root"
  add_allowed "$work_config_root"
  add_allowed "$knowledge_vault_root"
  knowledge_relative=${knowledge_vault_root#/}
fi
allowed_file=$(mktemp)
manifest_file=$(mktemp)
decrypt() { age --decrypt --identity "$frozen_identity" "$frozen_backup"; }
privileged_path_exists() { sudo test -e "$1" || sudo test -L "$1"; }
if [[ -n "$migration_journal" ]]; then
  # Migration-only root contract. Build the allowlist from the exact roots the
  # authenticated 3.2 backup declares and bind it to the contract evidence that
  # the clean-migration plan/rehearsal recorded. Standard restore never does this
  # and keeps its strict target allowlist (control/frontier roots included), so a 3.2
  # manifest that drifts from the exact authenticated contract is rejected.
  decrypt | python3 "$ROOT/scripts/read-backup-manifest.py" > "$allowed_file" || pixel_die "Authenticated 3.2 root contract could not be read"
  contract_observed=$(sha256sum "$allowed_file" | awk '{print $1}')
  [[ "$contract_observed" == "$PIXEL_MIGRATION_CONTRACT_SHA256" ]] || pixel_die "Backup root contract does not match the planned migration contract"
else
  printf '%s\n' "${allowed[@]}" | sort -u > "$allowed_file"
fi
audit=$(decrypt | python3 "$ROOT/scripts/audit-private-backup.py" --allowed "$allowed_file" --manifest-output "$manifest_file")
[[ $(sha256sum "$frozen_backup" | awk '{print $1}') == "$expected" ]] || pixel_die "Encrypted backup changed during validation"
if [[ $mode == validate ]]; then
  printf '%s\n' "$audit"
  exit 0
fi

stage=$(sudo mktemp -d /var/tmp/pixel-restore.XXXXXX)
[[ "$stage" == /var/tmp/pixel-restore.* ]] || pixel_die "Unsafe restore staging directory"
decrypt | sudo tar -xzf - -C "$stage" --delay-directory-restore --same-owner --same-permissions
[[ $(sha256sum "$frozen_backup" | awk '{print $1}') == "$expected" ]] || pixel_die "Encrypted backup changed during staging"
python3 "$ROOT/scripts/audit-private-backup.py" --allowed "$allowed_file" --manifest-output "$manifest_file.recheck" < <(decrypt) >/dev/null
cmp -s "$manifest_file" "$manifest_file.recheck" || pixel_die "Backup manifest changed between validation passes"
rm -f -- "$manifest_file.recheck"

if [[ $mode == rehearse ]]; then
  [[ "$rehearsal" == /* && "$rehearsal" != / ]] || pixel_die "Rehearsal root must be absolute and non-root"
  rehearsal=$(realpath -m "$rehearsal")
  [[ ! -e "$rehearsal" && ! -L "$rehearsal" ]] || pixel_die "Rehearsal root must not already exist"
  case "$rehearsal/" in
    "$OPENCLAW_HOME/"*|"$PIXEL_WORKSPACE/"*) pixel_die "Rehearsal root must be outside live Pixel state" ;;
  esac
  install -d -m 700 "$rehearsal"
  sudo cp -a -- "$stage/." "$rehearsal/"
  sudo rm -f -- "$rehearsal/.pixel-backup-manifest.json"
  printf '%s\n' '{"status":"pass","mode":"rehearse","liveStateChanged":false}'
  exit 0
fi

if [[ -n "$receipt" ]]; then
  source_pixel=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("pixelVersion","")[:64])' "$audit")
  target_pixel=$(pixel_read_release_version "$ROOT")
  [[ "$source_pixel" == "3.2.2" ]] || pixel_die "Source Pixel version is not 3.2.2"
  [[ "$target_pixel" == "4.3.27" ]] || pixel_die "Target Pixel version is not 4.3.27"
fi

mapfile -d '' roots < <(python3 - "$manifest_file" <<'PY'
import json, pathlib, sys
for value in json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["paths"]:
    sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
PY
)
declare -A allowed_map=()
for value in "${allowed[@]}"; do allowed_map[$value]=1; done
declare -A migration_noop_roots=()
migration_private_noop_file() {
  local source_path=$1 destination=$2 source_meta destination_meta source_uid source_mode source_links
  [[ -n "$migration_journal" ]] || return 1
  sudo test -f "$source_path" && ! sudo test -L "$source_path" || return 1
  sudo test -f "$destination" && ! sudo test -L "$destination" || return 1
  source_meta=$(sudo stat -c '%u:%g:%a:%h:%s' "$source_path") || return 1
  destination_meta=$(sudo stat -c '%u:%g:%a:%h:%s' "$destination") || return 1
  [[ "$source_meta" == "$destination_meta" ]] || return 1
  IFS=: read -r source_uid _ source_mode source_links _ <<<"$source_meta"
  [[ "$source_uid" == "$EUID" && "$source_links" == 1 ]] || return 1
  (( (8#$source_mode & 8#077) == 0 )) || return 1
  sudo cmp -s -- "$source_path" "$destination"
}
existing=0; knowledge_in_backup=0; knowledge_live=0
for relative in "${roots[@]}"; do
  destination="/$relative"
  if [[ -z ${allowed_map[$relative]+x} ]]; then
    # A 3.2 backup contains the legacy controller's private .env while the 4.3 controller
    # necessarily has a different PIXEL_ROOT. Migration may preserve such an obsolete root
    # only as a byte-and-metadata-identical owner-private regular-file no-op. It is never
    # passed to the privileged swap journal, so this exception cannot widen the destination
    # allowlist or mutate an arbitrary legacy path. Any drift fails closed.
    source_path="$stage/$relative"
    migration_private_noop_file "$source_path" "$destination" \
      || pixel_die "Backup requested a destination outside the local restore contract"
    migration_noop_roots[$relative]=1
    existing=1
    continue
  fi
  if privileged_path_exists "$destination"; then existing=1; fi
  if [[ -n $knowledge_relative && $relative == "$knowledge_relative" ]]; then
    knowledge_in_backup=1
    if privileged_path_exists "$destination"; then knowledge_live=1; fi
  fi
done
if [[ $existing == 1 && $replace == 0 ]]; then
  pixel_die "Live destinations already exist; use --replace --confirm only after review"
fi

units=(openclaw-gateway.service)
if [[ -n "$migration_journal" ]]; then
  # Migration affected-unit set: ALWAYS includes every fixed unit regardless of the desired
  # target enabled/active state. A previously running/enabled courier or broker that the
  # target disables must still be stopped+disabled (exact prestate), never left running just
  # because the target contract marks it inactive. Dynamic deep-work units are discovered
  # below and preserved at their captured prestate.
  units+=("${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}")
  units+=("${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}")
  units+=("${PIXEL_OPS_BROKER_UNIT:-pixel-ops-broker.service}")
  units+=("${PIXEL_FRONTIER_BROKER_UNIT:-pixel-frontier-broker.service}")
else
  [[ ${PIXEL_SOURCE_BROKER_ENABLED:-0} == 1 ]] && units+=("${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}")
  [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] && units+=("${PIXEL_OPS_BROKER_UNIT:-pixel-ops-broker.service}")
  [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] && units+=("${PIXEL_FRONTIER_BROKER_UNIT:-pixel-frontier-broker.service}")
  [[ ${PIXEL_WEB_COURIER_ENABLED:-0} == 1 ]] && units+=("${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}")
fi
work_units=()
if [[ ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} == 1 || -n "$migration_journal" ]]; then
  work_unit_output=$(pixel_systemctl_read list-units 'pixel-work-*' --all --state=active --plain --no-legend --no-pager) || pixel_die "Could not enumerate active Deep Work services before restore"
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    unit=${line%%[[:space:]]*}
    [[ $unit =~ ^pixel-work-[a-z0-9][a-z0-9.-]*\.(service|timer|path)$ ]] || pixel_die "Systemd returned an unsafe Deep Work unit name"
    work_units+=("$unit"); units+=("$unit")
  done <<<"$work_unit_output"
fi
# Deterministic de-duplication of the affected-unit set (the fixed units above plus any
# discovered dynamic deep-work units) so capture/quiesce/finalize always operate on an
# exact, ordered, unique list.
if [[ ${#units[@]} -gt 1 ]]; then
  mapfile -t units < <(printf '%s\n' "${units[@]}" | sort -u)
fi
temporary_paths=(); old_paths=(); destinations=(); had_old=()
transaction=1; committed=0
rollback_restore() {
  local index ok=1
  [[ ${defer_rollback:-0} == 1 ]] && return 0
  [[ ${transaction:-0} == 1 && ${committed:-0} == 0 ]] || return 0
  # Quiesce the exact validated units BEFORE any filesystem mutation. If a unit cannot be
  # stopped (or quiescence cannot be verified), restart every validated unit best-effort and
  # refuse to mutate. If live mutation has already begun, retain the reservation/backup with
  # a truthful diagnostic for manual recovery rather than silently aborting the only
  # rollback state; a reservation is never aborted after live mutation unless rollback was
  # positively verified.
  for unit in "${units[@]}"; do
    if ! pixel_systemctl stop "$unit" >/dev/null 2>&1; then
      for u in "${units[@]}"; do pixel_systemctl restart "$u" >/dev/null 2>&1 || true; done
      if [[ $live_mutation_started == 1 ]]; then retain_reservation=1; echo "[pixel] ERROR: pre-swap rollback could not quiesce services; the migration reservation and authenticated backup are retained for manual recovery" >&2; fi
      return 0
    fi
  done
  if [[ ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} == 1 ]]; then
    if ! pixel_systemctl stop 'pixel-work-*.path' 'pixel-work-*.timer' 'pixel-work-*.service' >/dev/null 2>&1; then
      for u in "${units[@]}"; do pixel_systemctl restart "$u" >/dev/null 2>&1 || true; done
      if [[ $live_mutation_started == 1 ]]; then retain_reservation=1; echo "[pixel] ERROR: pre-swap rollback could not quiesce Deep Work units; the migration reservation and authenticated backup are retained for manual recovery" >&2; fi
      return 0
    fi
    remaining_work_units=$(pixel_systemctl_read list-units 'pixel-work-*' --all --state=active --plain --no-legend --no-pager) || { for u in "${units[@]}"; do pixel_systemctl restart "$u" >/dev/null 2>&1 || true; done; if [[ $live_mutation_started == 1 ]]; then retain_reservation=1; echo "[pixel] ERROR: pre-swap rollback could not verify Deep Work quiescence; the migration reservation and authenticated backup are retained for manual recovery" >&2; fi; return 0; }
    if [[ -n $remaining_work_units ]]; then
      for u in "${units[@]}"; do pixel_systemctl restart "$u" >/dev/null 2>&1 || true; done
      if [[ $live_mutation_started == 1 ]]; then retain_reservation=1; echo "[pixel] ERROR: a Deep Work unit remained active during pre-swap rollback; the migration reservation and authenticated backup are retained for manual recovery" >&2; fi
      return 0
    fi
  fi
  for ((index=${#destinations[@]}-1; index>=0; index--)); do
    destination=${destinations[$index]}; old=${old_paths[$index]}; temporary=${temporary_paths[$index]}
    sudo rm -rf -- "$temporary" >/dev/null 2>&1 || true
    if [[ ${had_old[$index]} == 1 ]] && privileged_path_exists "$old"; then
      sudo rm -rf -- "$destination" >/dev/null 2>&1 || ok=0
      sudo mv -T -- "$old" "$destination" >/dev/null 2>&1 || ok=0
    elif [[ ${had_old[$index]} == 0 ]]; then
      sudo rm -rf -- "$destination" >/dev/null 2>&1 || ok=0
    fi
  done
  if [[ $ok == 1 ]]; then
    for unit in "${units[@]}"; do pixel_systemctl restart "$unit" >/dev/null 2>&1 || true; done
  elif [[ $live_mutation_started == 1 ]]; then
    retain_reservation=1
    echo "[pixel] ERROR: pre-swap rollback could not be verified after live mutation; the migration reservation and authenticated backup are retained for manual recovery" >&2
  fi
}
on_exit() {
  if [[ $journal_armed == 1 && $defer_rollback == 0 ]]; then
    # The full transaction journal was armed before any live destination rename, so any
    # ordinary failure between arm and the successful migration return must be undone by the
    # privileged helper's deterministic rollback (never the legacy inline best-effort
    # rollback), because a partial or full swap may already have occurred and the armed
    # journal is the only exact rollback authority. A hard kill (SIGKILL/power loss) never
    # runs this trap, so the journal stays armed for an explicit --migration-rollback.
    sudo python3 "$ROOT/scripts/restore-migration-journal.py" rollback \
      "$MIGRATION_CUSTODY" "$migration_journal" >/dev/null 2>&1 \
      || echo "[pixel] ERROR: migration rollback could not be completed automatically; the armed journal is retained for an explicit --migration-rollback" >&2
  elif [[ $reservation_active == 1 && $retain_reservation == 0 && -n "$migration_journal" && -n "$reservation_token" ]]; then
    # A captured migration reservation that failed before arm must be undone by the
    # privileged helper's exact abort (which restores the exact captured prestate), never
    # by the legacy blind restart-all rollback_restore. Live state was never mutated and
    # the only transition so far is the recorded quiescence, so abort restores exact
    # reality and removes the reservation.
    if ! sudo python3 "$ROOT/scripts/restore-migration-journal.py" abort \
      "$MIGRATION_CUSTODY" "$migration_journal" "$reservation_token" \
      "$PIXEL_MIGRATION_CONTRACT_SHA256" "$expected" >/dev/null 2>&1; then
      # Finding 9: a pre-arm helper abort / exact prestate restoration failure is never
      # silently swallowed. The reservation was NOT removed (abort only erases the marker
      # after exact restoration succeeds), so it and the authenticated backup are retained
      # for truthful explicit manual recovery - never imply exact restoration happened.
      # Mark the reservation as retained BEFORE cleanup so cleanup's own guarded abort
      # cannot silently retry and make this retained diagnostic stale/ambiguous. The
      # retained marker remains the explicit recovery authority.
      retain_reservation=1
      echo "[pixel] ERROR: pre-arm migration cleanup could not restore the exact service prestate; the migration reservation and authenticated backup are retained for manual recovery" >&2
    else
      # The exact abort restored service prestate and removed the reservation. The pre-arm
      # private-root temporary copies (created before arm, owned by root) are now explicitly
      # removed; a removal failure is reported truthfully and never treated as proof of a
      # complete cleanup.
      if [[ $temp_copies_created == 1 ]]; then
        _temp_clean_ok=1
        for temp in "${temporary_paths[@]}"; do
          sudo rm -rf -- "$temp" >/dev/null 2>&1 || _temp_clean_ok=0
        done
        if [[ $_temp_clean_ok == 0 ]]; then
          echo "[pixel] ERROR: pre-arm migration temporary copies could not be fully removed; review the prepared temporary paths manually" >&2
        fi
      fi
    fi
  else
    rollback_restore
  fi
  cleanup
}
trap on_exit EXIT
if [[ -n "$migration_journal" ]]; then
  # Capture the exact service prestate BEFORE any unit is stopped, durably in the reserved
  # journal (finding 3). Rollback/abort restore exact reality; the prestate is never
  # re-snapshotted after the outer stop.
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" capture \
    "$MIGRATION_CUSTODY" "$migration_journal" "$reservation_token" \
    "$PIXEL_MIGRATION_CONTRACT_SHA256" "$expected" "${units[@]}" \
    || pixel_die "Migration service prestate could not be captured; nothing was changed"
  # Helper-owned verified quiescence (finding 3): stop every affected active unit, verify
  # inactive, and record the transition durably. Any early failure before arm restores the
  # exact captured prestate (never a blind restart-all). This replaces the legacy manual
  # ``systemctl stop ... || true`` loop.
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" quiesce \
    "$MIGRATION_CUSTODY" "$migration_journal" "$reservation_token" \
    "$PIXEL_MIGRATION_CONTRACT_SHA256" "$expected" \
    || pixel_die "Migration service quiescence could not be verified; exact prestate restored and nothing was changed"
fi
# Finding 8: in a migration the legacy pixel-work-* wildcard stop is NEVER run after the
# helper's exact quiescence - the helper already stops exactly the captured affected units
# (including any discovered deep-work units) and the deployment lock prevents new work
# launches. Running the wildcard here could mutate a unit not in the captured prestate and
# defeat the exact journal contract. It is preserved for ordinary (non-migration) restore.
if [[ ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} == 1 && -z "$migration_journal" ]]; then
  pixel_systemctl stop 'pixel-work-*.path' 'pixel-work-*.timer' 'pixel-work-*.service' >/dev/null
  remaining_work_units=$(pixel_systemctl_read list-units 'pixel-work-*' --all --state=active --plain --no-legend --no-pager) || pixel_die "Could not verify Deep Work restore quiescence"
  [[ -z $remaining_work_units ]] || pixel_die "A Deep Work unit remained active during restore quiescence"
fi
pixel_retire_agent_sandboxes

if [[ $existing == 1 ]]; then
  [[ -n ${PIXEL_BACKUP_AGE_RECIPIENT:-} ]] || pixel_die "--replace requires PIXEL_BACKUP_AGE_RECIPIENT for an automatic pre-restore safety backup"
  safety_dir="$(dirname "$backup")/pre-restore-$(pixel_timestamp)-$$"
  bash "$ROOT/scripts/backup-private-state.sh" "$safety_dir" "$PIXEL_BACKUP_AGE_RECIPIENT" >/dev/null
fi

knowledge_prepared_source=''
stage_knowledge_key() {
  local source=$1 destination=$2 mode_value links
  [[ "$source" == /* && "$source" != / && "$source" != *$'\n'* && "$source" != *$'\r'* ]] || pixel_die "Knowledge-vault keys must be absolute paths"
  [[ $(realpath -m "$source") == "$source" ]] || pixel_die "Knowledge-vault key paths must be normalized"
  if ! sudo test -f "$source" || sudo test -L "$source"; then pixel_die "A knowledge-vault key is missing or unsafe"; fi
  mode_value=$(sudo stat -c %a "$source"); links=$(sudo stat -c %h "$source")
  [[ $mode_value =~ ^[0-7]{3,4}$ && $links == 1 ]] || pixel_die "A knowledge-vault key has unsafe metadata"
  (( (8#$mode_value & 8#077) == 0 )) || pixel_die "Knowledge-vault keys must not be group/world accessible"
  sudo install -o "$work_service_user" -g "$work_service_group" -m 600 -- "$source" "$destination"
}
if [[ $knowledge_in_backup == 1 ]]; then
  [[ $knowledge_vault_id =~ ^knowledgevault-[a-f0-9]{12}$ ]] || pixel_die "Restoring a knowledge vault requires --knowledge-vault-id"
  [[ -n $restored_knowledge_key ]] || pixel_die "Restoring a knowledge vault requires --restored-knowledge-key"
  restored_knowledge_key=$(deep_path "$restored_knowledge_key" "restored knowledge vault key")
  work_service_user=${PIXEL_WORK_SERVICE_USER:-pixel-work}; work_service_group=${PIXEL_WORK_SERVICE_GROUP:-pixel-work}
  [[ $work_service_user =~ ^[a-z_][a-z0-9_-]{0,31}$ && $work_service_user != root && $work_service_group =~ ^[a-z_][a-z0-9_-]{0,31}$ && $work_service_group != root ]] || pixel_die "Deep Work restore account is invalid"
  id "$work_service_user" >/dev/null 2>&1 || pixel_die "Deep Work restore account does not exist"
  node_path=$(command -v node); node_path=$(realpath -e "$node_path")
  [[ "$node_path" == /* && -x "$node_path" ]] || pixel_die "A trusted Node.js executable is required for knowledge-vault restore"
  knowledge_restore_stage=$(sudo mktemp -d /var/tmp/pixel-knowledge-restore.XXXXXX)
  [[ "$knowledge_restore_stage" == /var/tmp/pixel-knowledge-restore.* ]] || pixel_die "Unsafe knowledge restore staging directory"
  sudo chown "$work_service_user:$work_service_group" "$knowledge_restore_stage"; sudo chmod 700 "$knowledge_restore_stage"
  sudo install -d -o "$work_service_user" -g "$work_service_group" -m 700 "$knowledge_restore_stage/restored-key" "$knowledge_restore_stage/current-key"
  staged_restored_key="$knowledge_restore_stage/restored-key/pixel-knowledge-vault-key"
  staged_current_key="$knowledge_restore_stage/current-key/pixel-knowledge-vault-key"
  stage_knowledge_key "$restored_knowledge_key" "$staged_restored_key"
  stage_knowledge_key "$current_knowledge_key" "$staged_current_key"
  knowledge_prepared_source="$knowledge_restore_stage/vault"
  sudo cp -a -- "$stage/$knowledge_relative" "$knowledge_prepared_source"
  sudo chown -R --no-dereference "$work_service_user:$work_service_group" "$knowledge_prepared_source"
  if [[ $knowledge_live == 1 ]]; then
    knowledge_receipt=$(sudo -u "$work_service_user" "$node_path" "$ROOT/deploy/work-controller/knowledge-vault-restore-cli.mjs" reconcile \
      --authoritative-vault "$knowledge_vault_root" --restored-vault "$knowledge_prepared_source" --vault-id "$knowledge_vault_id" \
      --authoritative-credential "$staged_current_key" --restored-credential "$staged_restored_key" --target-credential "$staged_current_key") || pixel_die "Knowledge-vault tombstone reconciliation or key rotation failed"
  else
    knowledge_receipt=$(sudo -u "$work_service_user" "$node_path" "$ROOT/deploy/work-controller/knowledge-vault-restore-cli.mjs" adopt \
      --restored-vault "$knowledge_prepared_source" --vault-id "$knowledge_vault_id" --restored-credential "$staged_restored_key" --target-credential "$staged_current_key") || pixel_die "Knowledge-vault adoption or key rotation failed"
  fi
  python3 - "$knowledge_receipt" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
if value.get("status") != "pass" or value.get("plaintextExposed") is not False or value.get("residualHistoricalKeyWrapping") is not False:
    raise SystemExit("knowledge-vault restore evidence is incomplete")
PY
fi

# Pre-arm fold: compose every deployment item that overlaps EXACTLY one authenticated
# private root into that root's non-live restore staging tree BEFORE the root copy/swap.
# The privileged helper classifies every item as a pure invariant (never creating a
# newPath) and composes the folded candidates; ambiguous, inverse, unsafe-type, traversal,
# and symlink-ancestor cases are refused. Items that overlap no root stay standalone and
# are installed by the journal on the normal path.
fold_dests=()
for relative in "${roots[@]}"; do
  [[ -z ${migration_noop_roots[$relative]+x} ]] || continue
  fold_dests+=("/$relative")
done
if [[ -n "$migration_spec" ]]; then
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" fold     "$MIGRATION_CUSTODY" "$migration_spec" "$migration_stage" "$stage" "${fold_dests[@]}"     || pixel_die "Migration deployment fold failed; nothing was changed"
fi
index=0
for relative in "${roots[@]}"; do
  [[ -z ${migration_noop_roots[$relative]+x} ]] || continue
  source_path="$stage/$relative"; destination="/$relative"; parent=$(dirname "$destination"); name=$(basename "$destination")
  if [[ $knowledge_in_backup == 1 && $relative == "$knowledge_relative" ]]; then source_path=$knowledge_prepared_source; fi
  privileged_path_exists "$source_path" || pixel_die "Staged restore root is missing"
  sudo mkdir -p -- "$parent"
  temporary="$parent/.pixel-restore-$name-$$-$index.new"
  old="$parent/.pixel-restore-$name-$$-$index.old"
  if privileged_path_exists "$temporary" || privileged_path_exists "$old"; then pixel_die "Restore transaction path collision"; fi
  temporary_paths+=("$temporary"); old_paths+=("$old"); destinations+=("$destination")
  if privileged_path_exists "$destination"; then had_old+=(1); else had_old+=(0); fi
  # Mark the pre-arm temporary copies as created as soon as the first temp target is
  # established (before the copy), so a partial cp failure (set -e exit) still leaves the
  # flag set and the pre-arm abort cleans up every prepared temp path.
  temp_copies_created=1
  sudo cp -a -- "$source_path" "$temporary"
  index=$((index + 1))
done
if [[ -n "$migration_journal" ]]; then
  arm_args=( "$MIGRATION_CUSTODY" "$migration_journal" "$reservation_token" "$PIXEL_MIGRATION_CONTRACT_SHA256"
             "$expected" "${#destinations[@]}" "${destinations[@]}" "${old_paths[@]}" "${temporary_paths[@]}"
             "${had_old[@]}" "${units[@]}" )
  if [[ -n "$migration_spec" ]]; then
    # The privileged helper performs all candidate intake from the non-live prepare stage
    # during arm (--stage), descriptor-bound, under its exact allowlist, verifying each
    # candidate's prepared per-item digest. The ordinary restore shell never stages a
    # privileged path, never interprets arbitrary deployment paths, and never mutates a
    # newPath before arm. The strict deployment spec is passed to the journal arm (trailing
    # absolute path) so the same armed transaction protects the deployment-state transitions.
    arm_args+=( "$migration_spec" "--stage" "$migration_stage" )
  fi
  # Arm the full transaction journal durably BEFORE any live destination rename. The
  # privileged helper captures old-snapshot evidence from each live destination while it is
  # still at destination (the inode/device/type follows the content to oldPath if moved) and
  # fails closed if hadOld=1 but a destination is missing, hadOld=0 but a destination exists,
  # any oldPath already exists, or a prepared temporary path is missing/unsafe. Once armed,
  # any ordinary failure before the successful migration return is undone by the privileged
  # helper's deterministic rollback from the EXIT trap (never the legacy inline best-effort
  # rollback), and a hard kill (SIGKILL/power loss) leaves the armed journal so an explicit
  # --migration-rollback can deterministically reconstruct the roots.
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" arm "${arm_args[@]}"     || pixel_die "Migration journal could not be armed; nothing was changed"
  journal_armed=1
  if [[ -n "$migration_spec" ]]; then
    # Install (swap) the deployment-state items now that they are armed, BEFORE the
    # private-root renames. This is the mutation boundary: the helper quiesces the exact
    # validated units and swaps each deployment item, leaving units stopped for the
    # private-root swap below.
    sudo python3 "$ROOT/scripts/restore-migration-journal.py" install \
      "$MIGRATION_CUSTODY" "$migration_journal" \
      || pixel_die "Migration deployment install could not be completed; the armed journal is retained for rollback"
  fi
fi
for ((index=0; index<${#destinations[@]}; index++)); do
  # The full transaction journal was armed above, so this first live destination rename is
  # always covered: a hard kill here leaves the armed journal for deterministic resumable
  # rollback, and an ordinary failure is undone by the privileged helper from the EXIT trap.
  live_mutation_started=1
  if [[ ${had_old[$index]} == 1 ]]; then sudo mv -T -- "${destinations[$index]}" "${old_paths[$index]}"; fi
  sudo mv -T -- "${temporary_paths[$index]}" "${destinations[$index]}"
done
for relative in "${!migration_noop_roots[@]}"; do
  migration_private_noop_file "$stage/$relative" "/$relative" \
    || pixel_die "Preserved migration no-op root changed during activation; the pre-migration state is being reinstated"
done
if [[ $knowledge_in_backup == 1 ]]; then
  sudo -u "$work_service_user" "$node_path" "$ROOT/deploy/work-controller/knowledge-vault-restore-cli.mjs" verify \
    --vault "$knowledge_vault_root" --vault-id "$knowledge_vault_id" --credential "$staged_current_key" >/dev/null \
    || pixel_die "Activated knowledge vault failed its final deep audit"
fi
pixel_refresh_custom_plugin_registry
if [[ -n "$migration_journal" && -n "$migration_spec" ]]; then
  # Migration: apply the exact reviewed serviceDesired poststate through the privileged
  # helper (daemon-reload + enable/start/disable/stop + verify). Never a blind restart and
  # never a swallowed daemon-reload. Any failure keeps the armed journal for exact rollback.
  sudo python3 "$ROOT/scripts/restore-migration-journal.py" finalize \
    "$MIGRATION_CUSTODY" "$migration_journal" \
    || pixel_die "Migration service finalization failed; the armed journal is retained for exact rollback"
else
  pixel_systemctl daemon-reload >/dev/null 2>&1 || true
  for unit in "${units[@]}"; do pixel_systemctl restart "$unit" >/dev/null; done
fi
# Reader ACLs are target deployment policy, not authenticated legacy content. Rebuild
# only the bounded public projections after broker startup has regenerated its files and
# while rollback remains armed. A failure is covered by the exact existing rollback path;
# private policy, credentials, plans, approvals, and authority stay closed.
pixel_apply_broker_reader_acls
if ! bash "$ROOT/scripts/verify.sh" >/dev/null; then
  pixel_die "Restored state failed verification; the pre-restore state is being reinstated"
fi
if [[ -n "$migration_journal" ]]; then
  # Migration-only branch. The transaction journal was already armed before the first live
  # destination rename, so deterministic rollback is available the entire way through; the
  # restore receipt is finalized while rollback is still armed, and control returns to the
  # clean-migration activate WITHOUT committing. Services are already restarted above (the
  # outer target verify may require them running), and they stay running through the outer
  # verify while rollback remains armed; pre-migration old paths are preserved and control
  # returns to the clean-migration activate WITHOUT committing. Activate then runs
  # --migration-commit on success or --migration-rollback on any post-swap failure. Standard
  # restores never take this branch.
  if [[ -n "$receipt" ]]; then
    if ! python3 "$ROOT/scripts/restore-receipt.py" finalize "$receipt" "$expected" "$source_pixel" "$target_pixel" "$ROOT" "$knowledge_in_backup"; then
      pixel_die "Restore succeeded but restore-receipt finalization failed; the pre-migration state is being reinstated"
    fi
    receipt_finalized=1
  fi
  defer_rollback=1
  printf '%s\n' '{"status":"pass","mode":"restore-migration","verified":true,"automaticRollbackArmed":true,"committed":false}'
  exit 0
fi
committed=1
if [[ -n "$receipt" ]]; then
  # Every fallible pre-commit receipt preparation step (the atomic reserve) is already
  # complete, so the old rollback paths are only removed after the reservation has been
  # finalized into the exact pass receipt. A rare finalization failure is reported as
  # content-free and state-accurate: restore succeeded but the receipt could not be
  # finalized, so migration finalize must not run.
  if ! python3 "$ROOT/scripts/restore-receipt.py" finalize "$receipt" "$expected" "$source_pixel" "$target_pixel" "$ROOT" "$knowledge_in_backup"; then
    echo "[pixel] ERROR: restore succeeded but receipt finalization failed; do not run migration finalize" >&2
    exit 1
  fi
  receipt_finalized=1
fi
for old in "${old_paths[@]}"; do sudo rm -rf -- "$old"; done
if [[ $knowledge_in_backup == 1 ]]; then
  printf '%s\n' '{"status":"pass","mode":"restore","verified":true,"automaticRollbackArmed":true,"knowledgeDeletionReconciled":true,"historicalKeyWrappingRemoved":true}'
else
  printf '%s\n' '{"status":"pass","mode":"restore","verified":true,"automaticRollbackArmed":true,"knowledgeDeletionReconciled":false,"historicalKeyWrappingRemoved":false}'
fi
