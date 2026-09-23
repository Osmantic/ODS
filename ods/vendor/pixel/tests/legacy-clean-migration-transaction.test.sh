#!/usr/bin/env bash
# Real executable test of the migration-only control verbs in restore-private-state.sh.
#
# It invokes the actual --migration-commit / --migration-rollback verbs (including the real
# journal parser, custody-directory validation, exclusive custody lock, derived-sibling/
# contract/ownership validation, and the cleanup/finalization state machine) inside a
# completely isolated temporary tree, with fake sudo/systemctl helpers. It never touches
# sudo, systemd, /var/lib, /etc, live Pixel state, private backups, identities, or
# credentials. A crafted journal can never delete or move a sentinel outside the allowed
# contract, rollback restores every pre-state byte and service state, rollback and commit
# are idempotently resumable after a service-restart failure without re-mutating the
# filesystem, a cleanup failure never triggers rollback or destroys the verified new live
# state, concurrent commit-vs-rollback lets exactly one terminal path win, and ordinary 4.3
# restore keeps its strict allowlist.
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

repo="$tmp/repo"
mkdir -p "$repo"
tar --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist \
  --exclude=__pycache__ --exclude='*.pyc' -C "$SOURCE" -cf - . | tar -xf - -C "$repo"
mkdir -p "$tmp/install" "$tmp/openclaw"
cat > "$repo/.env" <<ENV
PIXEL_INSTALL_DIR=$tmp/install
PIXEL_RELEASE_VERSION=4.3.27
OPENCLAW_HOME=$tmp/openclaw
OPENCLAW_BIN=$tmp/openclaw-bin
ENV

# Fake sudo: executes the real command so a buggy control verb would actually delete/move the
# sentinel. An optional failure matcher lets the harness simulate a privileged cleanup failure.
# Real sudo runs as root; here sudo runs as the test user, so an explicit root owner change on
# a freshly written journal is a no-op (the journal stays owned by the test euid, which the
# parser requires). There is no test bypass an actual root parser would accept.
fake="$tmp/bin"; mkdir -p "$fake"
cat > "$fake/sudo" <<'SUDO'
#!/usr/bin/env bash
if [[ -n ${PIXEL_MIGRATION_TEST_SUDO_FAIL:-} ]]; then
  case " $* " in
    *" $PIXEL_MIGRATION_TEST_SUDO_FAIL "*) echo "simulated privileged failure: $*" >&2; exit 1 ;;
  esac
fi
# Real sudo runs as root; here sudo runs as the test user, so an explicit root owner change
# is mapped to the invoking user (no-op for chown, translated for install). This only maps
# ownership in the fake privileged environment; the parser still requires owner==euid, so
# there is no bypass an actual root parser would accept.
new_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    chown) if [[ ${2:-} == root:root ]]; then shift 2; else new_args+=("$1"); shift; fi ;;
    -o) new_args+=("$1"); shift; if [[ ${1:-} == root ]]; then new_args+=("$(id -un)"); else new_args+=("$1"); fi; shift ;;
    -g) new_args+=("$1"); shift; if [[ ${1:-} == root ]]; then new_args+=("$(id -gn)"); else new_args+=("$1"); fi; shift ;;
    *) new_args+=("$1"); shift ;;
  esac
done
exec "${new_args[@]}"
SUDO
chmod 700 "$fake/sudo"


# Fake systemctl: records every restart and can simulate a service-restart failure.
cat > "$fake/systemctl" <<'SYS'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
if [[ -n ${PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL:-} && "$*" == *"$PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL"* ]]; then
  echo "simulated systemctl failure: $*" >&2
  exit 1
fi
state="${PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE:-}"
cmd=$1
# The privileged helper invokes ``systemctl is-active UNIT`` (no --quiet), so the unit is the
# LAST argument, never the second (which would be "--quiet").
unit="${!#}"
case "$cmd" in
  stop) if [[ -n "$state" && -f "$state" ]]; then sed -i "\|^$unit$|d" "$state"; fi ;;
  start|restart) if [[ -n "$state" ]]; then printf '%s\n' "$unit" >> "$state"; fi ;;
  is-active) if [[ -n "$state" && -f "$state" ]] && grep -Fqx "$unit" "$state"; then echo active; exit 0; else echo inactive; exit 1; fi ;;
esac
exit 0
SYS
chmod 700 "$fake/systemctl"

# Fake rm/mv: the privileged helper now invokes fixed absolute rm/mv directly (no nested
# sudo/PATH), so the isolated non-root test copy points those constants at these fakes, which
# delegate to the real tools and can simulate a privileged removal failure.
cat > "$fake/rm" <<'RM'
#!/usr/bin/env bash
if [[ -n ${PIXEL_MIGRATION_TEST_RM_FAIL:-} && "$*" == *"$PIXEL_MIGRATION_TEST_RM_FAIL"* ]]; then
  echo "simulated rm failure: $*" >&2; exit 1
fi
exec /usr/bin/rm "$@"
RM
chmod 700 "$fake/rm"
cat > "$fake/mv" <<'MV'
#!/usr/bin/env bash
if [[ -n ${PIXEL_MIGRATION_TEST_MV_FAIL:-} && "$*" == *"$PIXEL_MIGRATION_TEST_MV_FAIL"* ]]; then
  echo "simulated mv failure: $*" >&2; exit 1
fi
exec /usr/bin/mv "$@"
MV
chmod 700 "$fake/mv"

# The privileged helper is copied to a clearly non-root test copy: REQUIRE_ROOT is disabled
# and the fixed absolute systemctl/rm/mv binaries are replaced with the isolated fakes. There
# is no environment override a production root euid would accept.
helper="$repo/scripts/restore-migration-journal.py"
sed -e 's|SYSTEMCTL_BIN = "/usr/bin/systemctl"|SYSTEMCTL_BIN = "'"$fake"'/systemctl"|' \
    -e 's|RM_BIN = "/usr/bin/rm"|RM_BIN = "'"$fake"'/rm"|' \
    -e 's|MV_BIN = "/usr/bin/mv"|MV_BIN = "'"$fake"'/mv"|' \
    -e 's|^REQUIRE_ROOT = True|REQUIRE_ROOT = False|' \
    "$SOURCE/scripts/restore-migration-journal.py" > "$helper"

# The isolated custody directory substitutes the hardcoded production custody root
# (/var/lib/pixel-migration-journals) via a mechanical copy of the script. Ownership is
# still derived only from the parser's effective uid.
custody="$tmp/custody"; mkdir -p "$custody"; chmod 700 "$custody"
script="$repo/scripts/restore-private-state-custody.sh"
sed "s|/var/lib/pixel-migration-journals|$custody|g" "$repo/scripts/restore-private-state.sh" > "$script"
chmod 700 "$script"

export PATH="$fake:$PATH"
export PIXEL_SYSTEMCTL_BIN="$fake/systemctl"
export PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG="$tmp/systemctl.log"
export PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE="$tmp/systemctl.state"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"

fail() { echo "legacy-clean-migration transaction: $*" >&2; exit 1; }

run_verb() { # mode journal -> sets rc, stdout, stderr
  rc=0; stdout=''; stderr=''
  set +e
  stdout=$(cd "$repo" && bash "$script" "$1" "$2" 2>"$tmp/err")
  rc=$?
  set -e
  stderr=$(cat "$tmp/err")
}

write_journal() { # file json
  printf '%s\n' "$2" > "$1"
  chmod 600 "$1"
}

ev() { # path -> evidence json matching the privileged evidence algorithm (ino/dev/size/type)
  python3 - "$1" <<'PY'
import os, stat, sys
info = os.lstat(sys.argv[1])
if stat.S_ISDIR(info.st_mode): kind = "dir"
elif stat.S_ISREG(info.st_mode): kind = "file"
elif stat.S_ISLNK(info.st_mode): kind = "symlink"
else: kind = "other"
print('{"ino":%d,"dev":%d,"size":%d,"type":"%s"}' % (info.st_ino, info.st_dev, info.st_size, kind))
PY
}
journal_json() { # committed cleanup rolledBack finalization dest old new had_old -> json
  local committed=$1 cleanup=$2 rolledBack=$3 finalization=$4 dest=$5 old=$6 new=$7 had=$8
  local old_ev='null'
  if [[ $had == 1 && -e "$old" ]]; then old_ev=$(ev "$old"); fi
  printf '{"schemaVersion":1,"kind":"pixel-restore-migration-journal","backupSha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sourcePixel":"3.2.2","targetPixel":"4.3.27","committed":%s,"cleanup":"%s","rolledBack":%s,"finalization":"%s","contractRoots":["%s"],"destinations":["%s"],"oldPaths":["%s"],"temporaryPaths":["%s"],"hadOld":[%s],"units":["openclaw-gateway.service"],"rollbackProgress":["pending"],"oldEvidence":[%s]}' \
    "$committed" "$cleanup" "$rolledBack" "$finalization" "$dest" "$dest" "$old" "$new" "$had" "$old_ev"
}

# --------------------------------------------------------------------------- #
# Scenario 1: post-swap rollback restores every pre-state byte and service state.
# --------------------------------------------------------------------------- #
root="$tmp/root"; state="$tmp/state"; mkdir -p "$root" "$state"
journal="$custody/txn.json"
dest="$root/data"
old="$root/.pixel-restore-data-999-0.old"
new="$root/.pixel-restore-data-999-0.new"
printf 'PRE-MIGRATION-OLD\n' > "$old"
printf 'NEW-LIVE-STATE\n' > "$dest"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
run_verb --migration-rollback "$journal"
[[ $rc -eq 0 ]] || fail "rollback failed: $stderr"
grep -Eq '"rolledBack":[[:space:]]*true' "$journal" || fail "rollback did not record verified rollback evidence"
grep -Eq '"finalization":[[:space:]]*"complete"' "$journal" || fail "rollback did not record service finalization"
[[ "$(cat "$dest")" == "PRE-MIGRATION-OLD" ]] || fail "rollback did not restore every pre-state byte"
[[ ! -e "$old" ]] || fail "rollback left the pre-migration old path behind"
grep -Fq "restart openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG" \
  || fail "rollback did not restart the Pixel service"

# --------------------------------------------------------------------------- #
# Scenario 2: terminal commit is idempotent and preserves the new live state.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
run_verb --migration-commit "$journal"
[[ $rc -eq 0 ]] || fail "commit failed: $stderr"
if ! grep -Eq '"committed":[[:space:]]*true' "$journal" || ! grep -Eq '"cleanup":[[:space:]]*"complete"' "$journal" || ! grep -Eq '"finalization":[[:space:]]*"complete"' "$journal"; then
  fail "commit did not mark committed, cleanup complete, and finalization complete"
fi
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "commit destroyed the verified new live state"
[[ ! -e "$old" ]] || fail "commit did not delete the old state"
run_verb --migration-commit "$journal"   # idempotent second run
[[ $rc -eq 0 ]] || fail "idempotent re-commit failed: $stderr"
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "re-commit altered the new live state"

# --------------------------------------------------------------------------- #
# Scenario 3: a cleanup failure retains the verified new live state, records
# cleanup=failed truthfully, and resumes idempotently without rolling back.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
PIXEL_MIGRATION_TEST_RM_FAIL="$old" run_verb --migration-commit "$journal"
[[ $rc -ne 0 ]] || fail "commit with cleanup failure unexpectedly succeeded"
if ! grep -Eq '"committed":[[:space:]]*true' "$journal" || ! grep -Eq '"cleanup":[[:space:]]*"failed"' "$journal"; then
  fail "cleanup failure did not record committed+cleanup-failed truthfully"
fi
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "cleanup failure destroyed the verified new live state"
[[ -e "$old" ]] || fail "cleanup failure deleted old state it could not verify"
grep -Fq '"status":"cleanup-failed"' <<<"$stdout" || fail "cleanup failure did not report cleanup-failed"
# Idempotent resume completes the cleanup without re-swapping or rolling back.
unset PIXEL_MIGRATION_TEST_RM_FAIL
run_verb --migration-commit "$journal"
[[ $rc -eq 0 ]] || fail "cleanup resume failed: $stderr"
grep -Eq '"cleanup":[[:space:]]*"complete"' "$journal" || fail "cleanup resume did not complete"
grep -Eq '"finalization":[[:space:]]*"complete"' "$journal" || fail "cleanup resume did not finalize services"
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "cleanup resume altered the new live state"

# --------------------------------------------------------------------------- #
# Scenario 4: a crafted journal cannot delete/move a sentinel outside the allowed
# contract (arbitrary old paths, contract mismatch, malicious units, committed
# rollback, unsafe mode, out-of-custody path, and inexact or mismatched siblings).
# --------------------------------------------------------------------------- #
sentinel="$state/sentinel.txt"
printf 'SENTINEL\n' > "$sentinel"

# 4a: arbitrary oldPaths pointing at the sentinel (not an exact derived sibling).
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$sentinel" "$new" 1)"
run_verb --migration-commit "$journal"
[[ $rc -ne 0 ]] || fail "commit accepted an arbitrary old path"
[[ -e "$sentinel" ]] || fail "arbitrary old path deleted the sentinel"
grep -Fq "transaction sibling" <<<"$stderr" || fail "arbitrary old path was not rejected by sibling derivation"

# 4b: a destination outside the trusted contract (destinations != contractRoots).
write_journal "$journal" "{\"schemaVersion\":1,\"kind\":\"pixel-restore-migration-journal\",\"backupSha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"sourcePixel\":\"3.2.2\",\"targetPixel\":\"4.3.27\",\"committed\":false,\"cleanup\":\"armed\",\"rolledBack\":false,\"finalization\":\"armed\",\"contractRoots\":[\"$dest\"],\"destinations\":[\"$sentinel\"],\"oldPaths\":[\"$state/.pixel-restore-sentinel.txt-999-0.old\"],\"temporaryPaths\":[\"$state/.pixel-restore-sentinel.txt-999-0.new\"],\"hadOld\":[1],\"units\":[\"openclaw-gateway.service\"],\"rollbackProgress\":[\"pending\"],\"oldEvidence\":[$(ev "$sentinel")]}"
run_verb --migration-rollback "$journal"
[[ $rc -ne 0 ]] || fail "rollback accepted a destination outside the trusted contract"
[[ -e "$sentinel" ]] || fail "contract-mismatched rollback deleted the sentinel"
grep -Fq "contract membership" <<<"$stderr" || fail "outside-contract destination was not rejected"

# 4c: malicious unit names are rejected.
printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
sed -i 's#"openclaw-gateway.service"#"evil.service;rm -rf /"#' "$journal"
run_verb --migration-commit "$journal"
[[ $rc -ne 0 ]] || fail "commit accepted a unit outside the fixed service contract"
grep -Fq "fixed Pixel service contract" <<<"$stderr" || fail "malicious unit was not rejected"

# 4d: rollback of an already-committed transaction is refused.
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json true pending false armed "$dest" "$old" "$new" 1)"
run_verb --migration-rollback "$journal"
[[ $rc -ne 0 ]] || fail "rollback of a committed transaction was accepted"
grep -Fq "already-committed" <<<"$stderr" || fail "committed rollback was not rejected"

# 4e: an unsafe journal mode is rejected by the privileged parser.
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
chmod 644 "$journal"
run_verb --migration-commit "$journal"
[[ $rc -ne 0 ]] || fail "commit accepted a journal with unsafe mode"
grep -Fq "owner-only 0600" <<<"$stderr" || fail "unsafe-mode journal was not rejected by the parser"
chmod 600 "$journal"

# 4f: a journal outside the fixed custody directory is rejected by the shell gate.
outside_journal="$state/outside.json"
write_journal "$outside_journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
run_verb --migration-commit "$outside_journal"
[[ $rc -ne 0 ]] || fail "commit accepted a journal outside the custody directory"
grep -Fq "inside the custody directory" <<<"$stderr" || fail "out-of-custody journal was not rejected"

# 4g: an inexact derived sibling (extra stem characters) is rejected exactly.
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$root/.pixel-restore-databla-999-0.old" "$root/.pixel-restore-databla-999-0.new" 1)"
run_verb --migration-commit "$journal"
[[ $rc -ne 0 ]] || fail "commit accepted an inexact derived sibling"
grep -Fq "transaction sibling is not exactly derived" <<<"$stderr" || fail "inexact sibling was not rejected exactly"

# 4h: old and new siblings must share the identical pid/index token.
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$root/.pixel-restore-data-999-0.old" "$root/.pixel-restore-data-998-0.new" 1)"
run_verb --migration-commit "$journal"
[[ $rc -ne 0 ]] || fail "commit accepted mismatched pid/index siblings"
grep -Fq "share the identical pid/index token" <<<"$stderr" || fail "mismatched sibling token was not rejected"

# --------------------------------------------------------------------------- #
# Scenario 5: rollback failure is reported conservatively (verb exits non-zero and
# records no false rolledBack evidence when it cannot restore the exact state).
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
rm -f "$old"   # the old path disappears between arming and rollback
run_verb --migration-rollback "$journal"   # old path is missing, so exact restoration cannot be proven
[[ $rc -ne 0 ]] || fail "rollback with a missing old path unexpectedly succeeded"
grep -Eq '"rolledBack":[[:space:]]*true' "$journal" && fail "rollback claimed verified restoration it could not prove"
grep -Fq "could not be completed exactly" <<<"$stderr" || fail "rollback failure was not reported conservatively"

# --------------------------------------------------------------------------- #
# Scenario 6: a service-restart failure during rollback leaves data restoration
# durably marked; re-entry skips filesystem mutation and retries only services.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL="restart openclaw-gateway.service" run_verb --migration-rollback "$journal"
[[ $rc -ne 0 ]] || fail "rollback with a service-restart failure unexpectedly succeeded"
grep -Eq '"rolledBack":[[:space:]]*true' "$journal" || fail "rollback did not durably mark data restoration"
grep -Eq '"finalization":[[:space:]]*"failed"' "$journal" || fail "rollback did not record the service failure"
[[ "$(cat "$dest")" == "PRE-MIGRATION-OLD" ]] || fail "rollback did not restore the pre-state bytes"
# Safe resume: data restoration is already durably marked, so re-entry must skip the
# filesystem mutation (the old path is gone) and retry only the service finalization.
unset PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL
run_verb --migration-rollback "$journal"
[[ $rc -eq 0 ]] || fail "rollback resume failed: $stderr"
grep -Eq '"finalization":[[:space:]]*"complete"' "$journal" || fail "rollback resume did not finalize services"
[[ "$(cat "$dest")" == "PRE-MIGRATION-OLD" ]] || fail "rollback resume re-mutated the restored destination"

# --------------------------------------------------------------------------- #
# Scenario 7: terminal commit never restarts services (they are already running and just
# passed the outer verify). Even if a restart would fail, commit succeeds and marks
# finalization complete truthfully without a disruptive restart.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL="restart openclaw-gateway.service" run_verb --migration-commit "$journal"
[[ $rc -eq 0 ]] || fail "commit unexpectedly restarted services (terminal commit must not restart)"
grep -Eq '"committed":[[:space:]]*true' "$journal" || fail "commit did not cross the terminal boundary"
grep -Eq '"cleanup":[[:space:]]*"complete"' "$journal" || fail "commit did not complete filesystem cleanup"
grep -Eq '"finalization":[[:space:]]*"complete"' "$journal" || fail "commit did not mark finalization complete without restart"
grep -Fq "restart openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG" \
  && fail "terminal commit issued a disruptive service restart"
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "commit service failure destroyed the new live state"
[[ ! -e "$old" ]] || fail "commit service failure left old state behind"
unset PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL

# --------------------------------------------------------------------------- #
# Scenario 8: concurrent commit-vs-rollback on the same journal lets exactly one
# terminal path win and leaves the state consistent.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
(cd "$repo" && bash "$script" --migration-commit "$journal" >"$tmp/c1.out" 2>"$tmp/c1.err") &
pid1=$!
(cd "$repo" && bash "$script" --migration-rollback "$journal" >"$tmp/c2.out" 2>"$tmp/c2.err") &
pid2=$!
set +e
wait "$pid1"; rc1=$?
wait "$pid2"; rc2=$?
set -e
wins=$(( (rc1 == 0) + (rc2 == 0) ))
[[ $wins -eq 1 ]] || fail "concurrent commit/rollback did not have exactly one winner (rc1=$rc1 rc2=$rc2)"
if grep -Eq '"committed":[[:space:]]*true' "$journal"; then
  grep -Eq '"rolledBack":[[:space:]]*false' "$journal" || fail "commit winner also recorded rollback"
  grep -Eq '"finalization":[[:space:]]*"complete"' "$journal" || fail "commit winner did not finalize"
  [[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "commit winner destroyed the new live state"
else
  grep -Eq '"rolledBack":[[:space:]]*true' "$journal" || fail "rollback winner did not record rollback"
  grep -Eq '"finalization":[[:space:]]*"complete"' "$journal" || fail "rollback winner did not finalize"
  [[ "$(cat "$dest")" == "PRE-MIGRATION-OLD" ]] || fail "rollback winner did not restore the pre-state bytes"
fi

# --------------------------------------------------------------------------- #
# Scenario 9: partial multi-root rollback is crash/idempotence safe. A privileged
# failure after root 1 (the last, processed first) succeeds must leave root 1's
# restored pre-state untouched on retry; a retry only finishes the failed root 0.
# --------------------------------------------------------------------------- #
rootA="$tmp/rootA"; rootB="$tmp/rootB"; mkdir -p "$rootA" "$rootB"
destA="$rootA/data"; oldA="$rootA/.pixel-restore-data-999-0.old"; newA="$rootA/.pixel-restore-data-999-0.new"
destB="$rootB/data"; oldB="$rootB/.pixel-restore-data-999-1.old"; newB="$rootB/.pixel-restore-data-999-1.new"
printf 'PRE-A\n' > "$oldA"; printf 'NEW-A\n' > "$destA"
printf 'PRE-B\n' > "$oldB"; printf 'NEW-B\n' > "$destB"
write_journal "$journal" "{\"schemaVersion\":1,\"kind\":\"pixel-restore-migration-journal\",\"backupSha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"sourcePixel\":\"3.2.2\",\"targetPixel\":\"4.3.27\",\"committed\":false,\"cleanup\":\"armed\",\"rolledBack\":false,\"finalization\":\"armed\",\"contractRoots\":[\"$destA\",\"$destB\"],\"destinations\":[\"$destA\",\"$destB\"],\"oldPaths\":[\"$oldA\",\"$oldB\"],\"temporaryPaths\":[\"$newA\",\"$newB\"],\"hadOld\":[1,1],\"units\":[\"openclaw-gateway.service\"],\"rollbackProgress\":[\"pending\",\"pending\"],\"oldEvidence\":[$(ev "$oldA"),$(ev "$oldB")]}"
# Rollback processes root index 1 (destB) first, then root index 0 (destA); inject a
# failure on destA's removal so root 1 is already restored when root 0 fails.
PIXEL_MIGRATION_TEST_RM_FAIL="$destA" run_verb --migration-rollback "$journal"
[[ $rc -ne 0 ]] || fail "two-root rollback with a root-0 failure unexpectedly succeeded"
[[ "$(cat "$destB")" == "PRE-B" ]] || fail "root 1 pre-state was lost after root 0 failed"
[[ ! -e "$oldB" ]] || fail "root 1 left its old path behind after root 0 failed"
[[ "$(cat "$destA")" == "NEW-A" ]] || fail "root 0 state changed unexpectedly"
grep -Eq '"rollbackProgress":[[:space:]]*\["in-progress"[[:space:]]*,[[:space:]]*"restored"\]' "$journal" \
  || fail "two-root journal did not record per-root progress after partial failure"
# Resume without the injected failure: root 0 finishes and root 1 is NOT reprocessed.
unset PIXEL_MIGRATION_TEST_RM_FAIL
run_verb --migration-rollback "$journal"
[[ $rc -eq 0 ]] || fail "two-root rollback resume failed: $stderr"
[[ "$(cat "$destA")" == "PRE-A" ]] || fail "root 0 was not restored on resume"
[[ "$(cat "$destB")" == "PRE-B" ]] || fail "root 1 was reprocessed on resume and lost its restored pre-state"
grep -Eq '"rolledBack":[[:space:]]*true' "$journal" || fail "two-root rollback did not record verified rollback evidence"

# --------------------------------------------------------------------------- #
# Scenario 10: a reserved (not-yet-armed) journal is rejected by the control verbs and
# is never erased or treated as an armed transaction.
# --------------------------------------------------------------------------- #
reserved="$custody/txn-reserved.json"
printf '%s\n' "{\"schemaVersion\":1,\"kind\":\"pixel-restore-migration-reserved\",\"reservationToken\":\"$(python3 -c 'import secrets;print(secrets.token_hex(16))')\",\"contractSha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"backupSha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"sourcePixel\":\"3.2.2\",\"targetPixel\":\"4.3.27\"}" > "$reserved"
chmod 600 "$reserved"
run_verb --migration-commit "$reserved"
[[ $rc -ne 0 ]] || fail "commit accepted a reserved (not-yet-armed) journal"
[[ -e "$reserved" ]] || fail "control verb erased a reserved (not-yet-armed) journal"
grep -Fq "must contain exactly the transaction keys" <<<"$stderr" \
  || fail "reserved journal was not rejected as not-armed"

# --------------------------------------------------------------------------- #
# Scenario 11: ordinary 4.3 restore keeps its strict allowlist and is never widened.
# --------------------------------------------------------------------------- #
grep -Fq -- '--migration is valid only for an actual confirmed restore with --receipt' "$repo/scripts/restore-private-state.sh" \
  || fail "migration mode is no longer guarded to a confirmed receipt restore"
# shellcheck disable=SC2016
grep -Fq 'printf '"'"'%s\n'"'"' "${allowed[@]}" | sort -u > "$allowed_file"' "$repo/scripts/restore-private-state.sh" \
  || fail "ordinary 4.3 restore allowlist construction was altered"
grep -Fq "PIXEL_MIGRATION_JOURNAL_OWNER" "$repo/scripts/restore-private-state.sh" \
  && fail "production owner override was not removed"

# --------------------------------------------------------------------------- #
# Scenario 12: two concurrent same-contract reservations allow exactly one owner,
# and no cross-abort or cross-arm is possible with the losing invocation's token.
# --------------------------------------------------------------------------- #
con_journal="$custody/txn-concurrent.json"
con_contract="$(printf 'concurrent' | sha256sum | awk '{print $1}')"
con_backup="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
conc_dest="$tmp/conc-dest"
conc_old="$tmp/.pixel-restore-conc-dest-999-0.old"
conc_new="$tmp/.pixel-restore-conc-dest-999-0.new"
# Arm now fails closed unless the prepared temporary path exists (and hadOld=0 means the
# destination must still be absent), so create the temp root the swap would have prepared.
mkdir -p "$conc_new"
# These captures are intentionally opened by the unprivileged test shell in its private temp
# directory; only the journal reservation helper itself runs with elevated privileges.
# shellcheck disable=SC2024
( sudo python3 "$repo/scripts/restore-migration-journal.py" reserve "$custody" "$con_journal" "$con_contract" "$con_backup" >"$tmp/t1" 2>"$tmp/t1e" ) &
pid1=$!
# shellcheck disable=SC2024
( sudo python3 "$repo/scripts/restore-migration-journal.py" reserve "$custody" "$con_journal" "$con_contract" "$con_backup" >"$tmp/t2" 2>"$tmp/t2e" ) &
pid2=$!
set +e
wait "$pid1"; rc1=$?
wait "$pid2"; rc2=$?
set -e
wins=$(( (rc1 == 0) + (rc2 == 0) ))
[[ $wins -eq 1 ]] || fail "concurrent same-contract reserve did not have exactly one owner (rc1=$rc1 rc2=$rc2)"
if [[ $rc1 -eq 0 ]]; then win_token=$(cat "$tmp/t1"); lose_token=$(cat "$tmp/t2" 2>/dev/null || true); else win_token=$(cat "$tmp/t2"); lose_token=$(cat "$tmp/t1" 2>/dev/null || true); fi
[[ -n "$win_token" && "$win_token" =~ ^[a-f0-9]{32}$ ]] || fail "reservation owner did not return a cryptographic token"
# The losing invocation's token must never arm or abort the winner's reservation.
set +e
sudo python3 "$repo/scripts/restore-migration-journal.py" abort "$custody" "$con_journal" "$lose_token" "$con_contract" "$con_backup" >/dev/null 2>"$tmp/aberr"
abrc=$?
set -e
[[ $abrc -ne 0 ]] || fail "cross-abort with the losing token succeeded"
[[ -e "$con_journal" ]] || fail "cross-abort with the losing token erased the winner's reservation"
set +e
sudo python3 "$repo/scripts/restore-migration-journal.py" arm "$custody" "$con_journal" "$lose_token" "$con_contract" "$con_backup" 1 "$conc_dest" "$conc_old" "$conc_new" 0 openclaw-gateway.service >/dev/null 2>"$tmp/armerr"
armrc=$?
set -e
[[ $armrc -ne 0 ]] || fail "cross-arm with the losing token succeeded"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-reserved"' "$con_journal"   || fail "cross-arm with the losing token armed the winner's reservation"
# The owner's own token arms the reservation exactly once.
sudo python3 "$repo/scripts/restore-migration-journal.py" arm "$custody" "$con_journal" "$win_token" "$con_contract" "$con_backup" 1 "$conc_dest" "$conc_old" "$conc_new" 0 openclaw-gateway.service   || fail "reservation owner could not arm its own reservation"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-journal"' "$con_journal"   || fail "reservation owner arm did not produce an armed journal"

# --------------------------------------------------------------------------- #
# Scenario 13: rollback must quiesce the exact validated units BEFORE any filesystem
# mutation; if a unit cannot be stopped, no rm/mv may occur and the live state stays
# intact. This fails the test whenever an rm/mv precedes the stop log.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL="stop openclaw-gateway.service" run_verb --migration-rollback "$journal"
[[ $rc -ne 0 ]] || fail "rollback with a quiescence (stop) failure unexpectedly succeeded"
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "rollback mutated the destination before quiescence succeeded"
[[ -e "$old" ]] || fail "rollback mutated the old path before quiescence succeeded"
grep -Fq "stop openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"   || fail "rollback did not attempt to quiesce the unit before refusing to mutate"
unset PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL
run_verb --migration-rollback "$journal"
[[ $rc -eq 0 ]] || fail "rollback after successful quiescence failed: $stderr"
[[ "$(cat "$dest")" == "PRE-MIGRATION-OLD" ]] || fail "rollback did not restore the pre-state after quiescence"

# --------------------------------------------------------------------------- #
# Scenario 14: a malicious string unit passed to arm is rejected BEFORE recording,
# because arm reuses the exact commit/rollback path/sibling/contract/unit validator.
# --------------------------------------------------------------------------- #
mal_journal="$custody/txn-malunit.json"
mal_contract="$(printf 'malunit' | sha256sum | awk '{print $1}')"
mal_backup="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
mal_dest="$tmp/mal-dest"
mal_old="$tmp/.pixel-restore-mal-dest-777-0.old"
mal_new="$tmp/.pixel-restore-mal-dest-777-0.new"
mkdir -p "$mal_new"
mal_token=$(sudo python3 "$repo/scripts/restore-migration-journal.py" reserve "$custody" "$mal_journal" "$mal_contract" "$mal_backup")
set +e
sudo python3 "$repo/scripts/restore-migration-journal.py" arm "$custody" "$mal_journal" "$mal_token" "$mal_contract" "$mal_backup" 1 "$mal_dest" "$mal_old" "$mal_new" 0 'evil.service;rm -rf /' >/dev/null 2>"$tmp/malerr"
malrc=$?
set -e
[[ $malrc -ne 0 ]] || fail "arm accepted a malicious string unit"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-reserved"' "$mal_journal" || fail "malicious-unit arm mutated the reserved marker"
grep -Fq "fixed Pixel service contract" "$tmp/malerr" || fail "malicious-unit arm was not rejected by the exact unit contract"

# --------------------------------------------------------------------------- #
# Scenario 14b: arm parses hadOld only as the literal strings 0 or 1. A truthy int
# coercion (bool(int(x))) would silently accept values like 2 or -1 as hadOld=1 and
# fabricate old-rollback evidence, so such flags must be refused before anything is
# recorded and must leave the reserved marker untouched.
# --------------------------------------------------------------------------- #
had_journal="$custody/txn-hadold.json"
had_contract="$(printf 'hadold' | sha256sum | awk '{print $1}')"
had_backup="dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
had_dest="$tmp/had-dest"
had_old="$tmp/.pixel-restore-had-dest-777-0.old"
had_new="$tmp/.pixel-restore-had-dest-777-0.new"
mkdir -p "$had_new"
had_token=$(sudo python3 "$repo/scripts/restore-migration-journal.py" reserve "$custody" "$had_journal" "$had_contract" "$had_backup")
for bad_had in 2 -1 00 true; do
  set +e
  sudo python3 "$repo/scripts/restore-migration-journal.py" arm "$custody" "$had_journal" "$had_token" "$had_contract" "$had_backup" 1 "$had_dest" "$had_old" "$had_new" "$bad_had" 'openclaw-gateway.service' >/dev/null 2>"$tmp/haderr"
  hadrc=$?
  set -e
  [[ $hadrc -ne 0 ]] || fail "arm accepted non-literal hadOld value '$bad_had'"
  grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-reserved"' "$had_journal" || fail "non-literal hadOld arm mutated the reserved marker"
  grep -Fq "hadOld must be the literal string 0 or 1" "$tmp/haderr" || fail "non-literal hadOld was not rejected by the literal 0/1 parser"
done

# --------------------------------------------------------------------------- #
# Scenario 14c: every verb requires the root euid in production. The production copy
# (REQUIRE_ROOT=True) must refuse even a read-only verb like inspect with the root
# gate message before touching custody, because a central gate is the only way inspect/
# ensure/reserve/abort (which do not invoke a privileged binary wrapper) are also
# root-gated. The isolated test copy (REQUIRE_ROOT=False) is what the scenarios above
# exercise, so this guards the real production script only.
# --------------------------------------------------------------------------- #
if [[ $(id -u) -ne 0 ]]; then
  set +e
  python3 "$SOURCE/scripts/restore-migration-journal.py" inspect "$custody" "$journal" >/dev/null 2>"$tmp/rootgate"
  rootrc=$?
  set -e
  [[ $rootrc -ne 0 ]] || fail "production script ran a verb without the root euid"
  grep -Fq "root euid" "$tmp/rootgate" || fail "production script did not enforce the central root-euid gate"
fi

# --------------------------------------------------------------------------- #
# Scenario 14d: arm fails closed on live-state that would make deterministic rollback
# impossible. Because arm runs BEFORE any live rename, it must refuse hadOld=1 with a
# missing destination, hadOld=0 with an existing destination, a pre-existing oldPath, a
# missing prepared temporary path, or an unsafe (non-directory) temporary path, and must
# leave the reserved marker untouched in every case.
# --------------------------------------------------------------------------- #
fc_backup="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
fc_arm() { # journal name hadold -> reserve and run one arm; rc/stderr captured via globals
  local j=$1 name=$2 had_flag=$3
  local c; c=$(printf 'fail-%s' "$name" | sha256sum | awk '{print $1}')
  local tok
  tok=$(sudo python3 "$repo/scripts/restore-migration-journal.py" reserve "$custody" "$j" "$c" "$fc_backup")
  local d="$tmp/fc-$name"
  local o="$tmp/.pixel-restore-fc-$name-555-0.old"
  local n="$tmp/.pixel-restore-fc-$name-555-0.new"
  set +e
  sudo python3 "$repo/scripts/restore-migration-journal.py" arm "$custody" "$j" "$tok" "$c" "$fc_backup" 1 "$d" "$o" "$n" "$had_flag" openclaw-gateway.service >/dev/null 2>"$tmp/fcerr-$name"
  fc_rc=$?
  set -e
  grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-reserved"' "$j" || fail "fail-closed arm for '$name' mutated the reserved marker"
}
# hadOld=1 but the destination is missing (temp prepared, old absent).
rm -rf "$tmp/fc-missing-dest" "$tmp/.pixel-restore-fc-missing-dest-555-0.old" "$tmp/.pixel-restore-fc-missing-dest-555-0.new"
mkdir -p "$tmp/.pixel-restore-fc-missing-dest-555-0.new"
fc_arm "$custody/txn-fail-missing.json" missing-dest 1
[[ $fc_rc -ne 0 ]] || fail "arm accepted hadOld=1 with a missing destination"
grep -Fq "live destination is missing" "$tmp/fcerr-missing-dest" || fail "arm did not reject a missing hadOld=1 destination"
# hadOld=0 but the destination exists.
rm -rf "$tmp/fc-exists-dest" "$tmp/.pixel-restore-fc-exists-dest-555-0.old" "$tmp/.pixel-restore-fc-exists-dest-555-0.new"
mkdir -p "$tmp/fc-exists-dest" "$tmp/.pixel-restore-fc-exists-dest-555-0.new"
fc_arm "$custody/txn-fail-exists.json" exists-dest 0
[[ $fc_rc -ne 0 ]] || fail "arm accepted hadOld=0 with an existing destination"
grep -Fq "live destination exists" "$tmp/fcerr-exists-dest" || fail "arm did not reject an existing hadOld=0 destination"
# oldPath already exists.
rm -rf "$tmp/fc-old-dest" "$tmp/.pixel-restore-fc-old-dest-555-0.old" "$tmp/.pixel-restore-fc-old-dest-555-0.new"
mkdir -p "$tmp/.pixel-restore-fc-old-dest-555-0.new" "$tmp/.pixel-restore-fc-old-dest-555-0.old"
fc_arm "$custody/txn-fail-old.json" old-dest 0
[[ $fc_rc -ne 0 ]] || fail "arm accepted a pre-existing oldPath"
grep -Fq "oldPath already exists" "$tmp/fcerr-old-dest" || fail "arm did not reject a pre-existing oldPath"
# prepared temporary path is missing.
rm -rf "$tmp/fc-notemp-dest" "$tmp/.pixel-restore-fc-notemp-dest-555-0.old" "$tmp/.pixel-restore-fc-notemp-dest-555-0.new"
fc_arm "$custody/txn-fail-notemp.json" notemp-dest 0
[[ $fc_rc -ne 0 ]] || fail "arm accepted a missing prepared temporary path"
grep -Fq "prepared temporary path is missing" "$tmp/fcerr-notemp-dest" || fail "arm did not reject a missing prepared temporary path"
# prepared temporary path is a regular single-link file: a top-level file temp root is a
# valid authenticated 3.2.2 backup root (e.g. etc/pixel-ops-broker.env), so arm must ACCEPT
# it and produce an armed journal with the destination-absence evidence.
rm -rf "$tmp/fc-file-dest" "$tmp/.pixel-restore-fc-file-dest-555-0.old" "$tmp/.pixel-restore-fc-file-dest-555-0.new"
printf 'safe-single-link-file\n' > "$tmp/.pixel-restore-fc-file-dest-555-0.new"
fc_file_journal="$custody/txn-ok-file.json"
fc_file_c=$(printf 'ok-file' | sha256sum | awk '{print $1}')
fc_file_tok=$(sudo python3 "$repo/scripts/restore-migration-journal.py" reserve "$custody" "$fc_file_journal" "$fc_file_c" "$fc_backup")
sudo python3 "$repo/scripts/restore-migration-journal.py" arm "$custody" "$fc_file_journal" "$fc_file_tok" "$fc_file_c" "$fc_backup" 1 "$tmp/fc-file-dest" "$tmp/.pixel-restore-fc-file-dest-555-0.old" "$tmp/.pixel-restore-fc-file-dest-555-0.new" 0 openclaw-gateway.service >/dev/null 2>"$tmp/fcerr-file"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-journal"' "$fc_file_journal" \
  || fail "arm rejected a safe regular single-link temp root"
# prepared temporary path is a symlink to a directory: unsafe (top-level temp root must be a
# real directory or regular file, never a link).
rm -rf "$tmp/fc-symlink-dest" "$tmp/.pixel-restore-fc-symlink-dest-555-0.old" "$tmp/.pixel-restore-fc-symlink-dest-555-0.new"
mkdir -p "$tmp/symlink-target-dir"
ln -s "$tmp/symlink-target-dir" "$tmp/.pixel-restore-fc-symlink-dest-555-0.new"
fc_arm "$custody/txn-fail-symlink.json" symlink-dest 0
[[ $fc_rc -ne 0 ]] || fail "arm accepted a symlink prepared temporary path"
grep -Fq "prepared temporary path is unsafe" "$tmp/fcerr-symlink-dest" || fail "arm did not reject a symlink prepared temporary path"
# prepared temporary path is a regular file with more than one hard link (aliased): unsafe.
rm -rf "$tmp/fc-hardlink-dest" "$tmp/.pixel-restore-fc-hardlink-dest-555-0.old" "$tmp/.pixel-restore-fc-hardlink-dest-555-0.new"
printf 'multi-link\n' > "$tmp/.pixel-restore-fc-hardlink-dest-555-0.new"
ln "$tmp/.pixel-restore-fc-hardlink-dest-555-0.new" "$tmp/hardlink-alias"
fc_arm "$custody/txn-fail-hardlink.json" hardlink-dest 0
[[ $fc_rc -ne 0 ]] || fail "arm accepted a multi-link regular file temp root"
grep -Fq "multi-link regular file" "$tmp/fcerr-hardlink-dest" || fail "arm did not reject a multi-link regular file temp root"
rm -f "$tmp/hardlink-alias"
# prepared temporary path is a special file (fifo): unsafe.
rm -rf "$tmp/fc-fifo-dest" "$tmp/.pixel-restore-fc-fifo-dest-555-0.old" "$tmp/.pixel-restore-fc-fifo-dest-555-0.new"
mkfifo "$tmp/.pixel-restore-fc-fifo-dest-555-0.new"
fc_arm "$custody/txn-fail-fifo.json" fifo-dest 0
[[ $fc_rc -ne 0 ]] || fail "arm accepted a fifo prepared temporary path"
grep -Fq "prepared temporary path is unsafe" "$tmp/fcerr-fifo-dest" || fail "arm did not reject a fifo prepared temporary path"

# --------------------------------------------------------------------------- #
# Scenario 15: if quiescence stops unit A but then fails to stop unit B, rollback must
# restart EVERY validated unit best-effort and fail without mutating or recording
# progress, so unit A is never left down by a failure on unit B.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "{\"schemaVersion\":1,\"kind\":\"pixel-restore-migration-journal\",\"backupSha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"sourcePixel\":\"3.2.2\",\"targetPixel\":\"4.3.27\",\"committed\":false,\"cleanup\":\"armed\",\"rolledBack\":false,\"finalization\":\"armed\",\"contractRoots\":[\"$dest\"],\"destinations\":[\"$dest\"],\"oldPaths\":[\"$old\"],\"temporaryPaths\":[\"$new\"],\"hadOld\":[1],\"units\":[\"openclaw-gateway.service\",\"pixel-source-broker.timer\"],\"rollbackProgress\":[\"pending\"],\"oldEvidence\":[$(ev "$old")]}"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
printf '%s\n' "openclaw-gateway.service" "pixel-source-broker.timer" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL="stop pixel-source-broker.timer" run_verb --migration-rollback "$journal"
[[ $rc -ne 0 ]] || fail "rollback with a partial quiescence failure unexpectedly succeeded"
[[ "$(cat "$dest")" == "NEW-LIVE-STATE" ]] || fail "partial quiescence failure mutated the destination"
grep -Fq "restart openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG" || fail "partial quiescence failure did not restart the first validated unit"
grep -Fq "restart pixel-source-broker.timer" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG" || fail "partial quiescence failure did not restart the second validated unit"
grep -Eq '"rolledBack":[[:space:]]*true' "$journal" && fail "partial quiescence failure recorded rollback progress"
unset PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL

# --------------------------------------------------------------------------- #
# Scenario 16: journal state transitions are crash-safe atomic replacements (a fresh
# inode via rename, no in-place ftruncate, no leftover temp), so a power loss can never
# leave the only rollback state truncated.
# --------------------------------------------------------------------------- #
rm -f "$dest" "$old"; printf 'NEW-LIVE-STATE\n' > "$dest"; printf 'PRE-MIGRATION-OLD\n' > "$old"
write_journal "$journal" "$(journal_json false armed false armed "$dest" "$old" "$new" 1)"
ino_before=$(stat -c %i "$journal")
run_verb --migration-commit "$journal"
[[ $rc -eq 0 ]] || fail "atomic commit failed: $stderr"
ino_after=$(stat -c %i "$journal")
[[ "$ino_before" != "$ino_after" ]] || fail "journal commit did not atomically replace (in-place write detected)"
find "$custody" -maxdepth 1 -name '.pixel-journal-*.tmp' | grep -q . && fail "atomic journal write left a temp file behind"
python3 - "$journal" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
assert value["committed"] is True and value["cleanup"] == "complete" and value["finalization"] == "complete"
PY

echo "legacy-clean-migration transaction control verbs behaved faithfully"
