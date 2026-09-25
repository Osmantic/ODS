#!/usr/bin/env bash
# Real end-to-end swap-branch test of restore-private-state.sh --migration.
#
# This actually invokes restore-private-state.sh in migration-only mode through validation,
# staging, the live swap, journal creation, post-swap verification failure/success, and then
# the real --migration-rollback / --migration-commit control verbs. It uses a synthetic
# signed age-pass-through backup, temp-only absolute roots, and fake sudo/systemctl/docker/
# openclaw/verify helpers. It never touches /etc, /var/lib, live Pixel state, real sudo,
# systemd, credentials, or private backups. It proves:
#   * the real EXIT trap reinstates every pre-migration byte when verification fails BEFORE
#     the journal is armed (no journal is written),
#   * an armed journal supports a real post-swap rollback (bytes + service state restored),
#   * an armed journal supports a real post-swap commit (old state deleted, new state kept),
#   * a standard (non-migration) restore rejects the migration-only root, so ordinary restore
#     strictness is proven by execution, not by a static grep.
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

repo="$tmp/repo"
mkdir -p "$repo"
tar --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist \
  --exclude=__pycache__ --exclude='*.pyc' -C "$SOURCE" -cf - . | tar -xf - -C "$repo"

# Temp-only absolute roots: the restore destination is /$relative, so the migration root is a
# path inside this test's temporary directory (never a real /etc or /var/lib path).
dest_root="$tmp/state-root"
relative="${dest_root#/}"
install="$tmp/install"
fake="$tmp/bin"; mkdir -p "$fake"
mkdir -p "$install" "$dest_root" "$tmp/openclaw" "$tmp/workspace" "$tmp/config/pixel-control" \
  "$tmp/config/pixel-deployment" "$tmp/ops-policy" "$tmp/frontier-policy"
printf 'OLD-LIVE-STATE\n' > "$dest_root/state.json"

cat > "$repo/.env" <<ENV
PIXEL_INSTALL_DIR=$install
PIXEL_RELEASE_VERSION=4.3.27
OPENCLAW_HOME=$tmp/openclaw
OPENCLAW_BIN=$fake/openclaw
PIXEL_WORKSPACE=$tmp/workspace
PIXEL_GOOGLE_TOKEN_PATH=$tmp/config/google-token.json
XDG_CONFIG_HOME=$tmp/config
PIXEL_CONTROL_POLICY_PATH=$tmp/config/pixel-control/policy.json
PIXEL_PRIVATE_ONBOARDING_PATH=$tmp/config/pixel-deployment/onboarding.json
PIXEL_SOURCE_BROKER_STATE_DIR=$dest_root
PIXEL_SOURCE_BROKER_ENV=$tmp/source.env
PIXEL_OPS_BROKER_STATE_DIR=$tmp/ops-state
PIXEL_OPS_BROKER_ENV=$tmp/ops.env
PIXEL_OPS_POLICY_PATH=$tmp/ops-policy/policy.json
PIXEL_FRONTIER_BROKER_STATE_DIR=$tmp/frontier-state
PIXEL_FRONTIER_BROKER_ENV=$tmp/frontier.env
PIXEL_FRONTIER_POLICY_PATH=$tmp/frontier-policy/policy.json
PIXEL_AGENT_ID=pixel
PIXEL_SOURCE_BROKER_ENABLED=0
PIXEL_OPS_BROKER_ENABLED=0
PIXEL_FRONTIER_BROKER_ENABLED=0
PIXEL_WEB_COURIER_ENABLED=0
PIXEL_DEEP_WORK_BACKUP_ENABLED=1
ENV
touch "$tmp/config/google-token.json" "$tmp/config/pixel-control/policy.json" \
  "$tmp/config/pixel-deployment/onboarding.json" "$tmp/source.env" "$tmp/ops.env" "$tmp/frontier.env"

# --------------------------------------------------------------------------- #
# Fake privileged / verification helpers.
# --------------------------------------------------------------------------- #
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


cat > "$fake/systemctl" <<'SYS'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
if [[ -n ${PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL:-} && "$*" == *"$PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL"* ]]; then
  echo "simulated systemctl failure: $*" >&2; exit 1
fi
state="${PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE:-}"
enabled="${PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED:-}"
notfound="${PIXEL_MIGRATION_TEST_SYSTEMCTL_NOTFOUND:-}"
cmd=$1
# The privileged helper invokes ``systemctl is-active --quiet UNIT`` / ``is-enabled --quiet
# UNIT``, so the unit is the LAST argument, never the second (which would be "--quiet").
unit="${!#}"
is_notfound() { [[ -n "$notfound" && -f "$notfound" ]] && grep -Fqx "$unit" "$notfound"; }
case "$cmd" in
  stop) if [[ -n "$state" && -f "$state" ]]; then sed -i "\|^$unit$|d" "$state"; fi ;;
  start|restart) if [[ -n "$state" ]]; then printf '%s\n' "$unit" >> "$state"; fi ;;
  enable) if [[ -n "$enabled" ]]; then printf '%s\n' "$unit" >> "$enabled"; fi ;;
  disable) if [[ -n "$enabled" && -f "$enabled" ]]; then sed -i "\|^$unit$|d" "$enabled"; fi ;;
  daemon-reload) : ;;
  is-active) if is_notfound; then echo not-found; exit 3; fi; if [[ -n "$state" && -f "$state" ]] && grep -Fqx "$unit" "$state"; then echo active; exit 0; else echo inactive; exit 1; fi ;;
  is-enabled) if is_notfound; then echo not-found; exit 4; fi; if [[ -n "$enabled" && -f "$enabled" ]] && grep -Fqx "$unit" "$enabled"; then echo enabled; exit 0; else echo disabled; exit 1; fi ;;
esac
exit 0
SYS
chmod 700 "$fake/systemctl"

# Fake rm/mv: the privileged helper invokes fixed absolute rm/mv directly (no nested
# sudo/PATH), so the isolated non-root test copy points those constants at these fakes,
# which delegate to the real tools.
cat > "$fake/rm" <<'RM'
#!/usr/bin/env bash
exec /usr/bin/rm "$@"
RM
chmod 700 "$fake/rm"
cat > "$fake/mv" <<'MV'
#!/usr/bin/env bash
# SIGKILL simulation hooks: a matched move performs the real rename (or not) and then
# SIGKILLs the restore shell so no EXIT trap runs, exactly like a hard kill/power loss in
# the middle of the live swap. The armed journal (written before the first rename) is then
# the only rollback authority. These hooks are inert unless the marker env vars are set.
if [[ -n ${PIXEL_MIGRATION_TEST_KILL_BEFORE_MV:-} && "$*" == *"$PIXEL_MIGRATION_TEST_KILL_BEFORE_MV"* ]]; then
  kill -9 "$PPID"
  exit 137
fi
if [[ -n ${PIXEL_MIGRATION_TEST_KILL_AFTER_MV:-} && "$*" == *"$PIXEL_MIGRATION_TEST_KILL_AFTER_MV"* ]]; then
  /usr/bin/mv "$@"
  kill -9 "$PPID"
  exit 137
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

cat > "$fake/docker" <<'DOCKER'
#!/usr/bin/env bash
exit 0
DOCKER
chmod 700 "$fake/docker"

cat > "$fake/age" <<'AGE'
#!/usr/bin/env bash
set -euo pipefail
output='' recipient='' identity='' mode=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --encrypt) mode=encrypt; shift ;;
    --decrypt) mode=decrypt; shift ;;
    --recipient) recipient=${2:?}; shift 2 ;;
    --identity) identity=${2:?}; shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    -) shift ;;
    *) input=$1; shift ;;
  esac
done
if [[ "$mode" == encrypt ]]; then
  [[ -n "$recipient" && -n "$output" ]] || { echo "fake age: recipient and output required" >&2; exit 2; }
  printf 'PIXEL-FAKE-AGE\nRECIPIENT:%s\n' "$recipient" > "$output"
  cat >> "$output"
elif [[ "$mode" == decrypt ]]; then
  [[ -n "$identity" && -f "$identity" && -n ${input:-} && -f "$input" ]] || { echo "fake age: identity and input required" >&2; exit 2; }
  expected=$(sed -n '2s/^RECIPIENT://p' "$input")
  actual=$(tr -d '\r\n' < "$identity")
  [[ -n "$expected" && "$actual" == "$expected" ]] || { echo "age: no identity matched" >&2; exit 1; }
  tail -n +3 "$input"
else
  echo "fake age: encrypt or decrypt mode required" >&2
  exit 2
fi
AGE
chmod 700 "$fake/age"

cat > "$fake/openclaw" <<'OC'
#!/usr/bin/env bash
if [[ ${1:-} == plugins ]]; then
  if [[ ${2:-} == registry ]]; then echo '{}'; exit 0; fi
fi
exit 0
OC
chmod 700 "$fake/openclaw"

# Verification and safety-backup helpers are isolated stubs inside the copied repo.
cat > "$repo/scripts/verify.sh" <<'VERIFY'
#!/usr/bin/env bash
[[ "${PIXEL_MIGRATION_TEST_VERIFY:-pass}" == "pass" ]]
VERIFY
chmod 700 "$repo/scripts/verify.sh"

cat > "$repo/scripts/backup-private-state.sh" <<'BKUP'
#!/usr/bin/env bash
echo "safety backup invoked: $*" >> "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
exit 0
BKUP
chmod 700 "$repo/scripts/backup-private-state.sh"

# --------------------------------------------------------------------------- #
# Synthetic signed age-pass-through backup.
# --------------------------------------------------------------------------- #
make_backup() { # out_path relative content
  local out_path="$1" relative="$2" content="$3" src="$tmp/bsrc" key="$tmp/backup-signing-key"
  rm -rf "$src"; mkdir -p "$src"
  printf '%s\n' "$content" > "$src/state.json"
  python3 - "$src" "$relative" "$tmp/payload.tar.gz" <<'PY'
import json, os, pathlib, sys, tarfile
src = pathlib.Path(sys.argv[1]); relative = sys.argv[2]; out = pathlib.Path(sys.argv[3])
manifest = {"schemaVersion": 1, "pixelVersion": "3.2.2", "paths": [relative]}
def add_dir(archive, name):
    info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = 0o700; info.size = 0
    info.uid = os.getuid(); info.gid = os.getgid()
    archive.addfile(info)
with tarfile.open(out, mode="w:gz") as archive:
    add_dir(archive, relative)
    for path in sorted(src.rglob("*")):
        name = relative + "/" + str(path.relative_to(src))
        data = path.read_bytes()
        info = tarfile.TarInfo(name); info.type = tarfile.REGTYPE; info.mode = 0o600; info.size = len(data)
        info.uid = os.getuid(); info.gid = os.getgid()
        archive.addfile(info, __import__("io").BytesIO(data))
    marker = tarfile.TarInfo(".pixel-backup-manifest.json")
    payload = json.dumps(manifest).encode()
    marker.mode = 0o600; marker.size = len(payload)
    marker.uid = os.getuid(); marker.gid = os.getgid()
    archive.addfile(marker, __import__("io").BytesIO(payload))
PY
  recipient="age1testrecipient"
  printf 'PIXEL-FAKE-AGE\nRECIPIENT:%s\n' "$recipient" > "$out_path"
  cat "$tmp/payload.tar.gz" >> "$out_path"
  ssh-keygen -q -Y sign -f "$key" -n pixel-private-backup "$out_path" >/dev/null
  sha256sum "$out_path" | awk '{print $1}' > "$out_path.sha256"
}
make_backup_multi() { # out_path spec_file key
  local out_path="$1" spec="$2" key="$3"
  python3 - "$spec" "$tmp/payload.tar.gz" <<'PY'
import json, os, pathlib, sys, tarfile, io
spec = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
out = pathlib.Path(sys.argv[2])
relatives = sorted(spec["roots"])
manifest = {"schemaVersion": 1, "pixelVersion": "3.2.2", "paths": relatives}
def add_dir(archive, name):
    info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = 0o700; info.size = 0
    info.uid = os.getuid(); info.gid = os.getgid()
    archive.addfile(info)
with tarfile.open(out, mode="w:gz") as archive:
    for relative in relatives:
        add_dir(archive, relative)
        for name, data in sorted(spec["roots"][relative].items()):
            member = relative + "/" + name
            payload = data.encode()
            info = tarfile.TarInfo(member); info.type = tarfile.REGTYPE; info.mode = 0o600; info.size = len(payload)
            info.uid = os.getuid(); info.gid = os.getgid()
            archive.addfile(info, io.BytesIO(payload))
    marker = tarfile.TarInfo(".pixel-backup-manifest.json")
    payload = json.dumps(manifest).encode()
    marker.mode = 0o600; marker.size = len(payload)
    marker.uid = os.getuid(); marker.gid = os.getgid()
    archive.addfile(marker, io.BytesIO(payload))
PY
  recipient="age1testrecipient"
  printf 'PIXEL-FAKE-AGE\nRECIPIENT:%s\n' "$recipient" > "$out_path"
  cat "$tmp/payload.tar.gz" >> "$out_path"
  ssh-keygen -q -Y sign -f "$key" -n pixel-private-backup "$out_path" >/dev/null
  sha256sum "$out_path" | awk '{print $1}' > "$out_path.sha256"
}
make_backup_with_noop_file() { # out_path directory_root file_root directory_content file_content key
  local out_path="$1" directory_root="$2" file_root="$3" directory_content="$4" file_content="$5" key="$6"
  python3 - "$directory_root" "$file_root" "$directory_content" "$file_content" "$tmp/payload-mixed.tar.gz" <<'PY'
import io, json, os, pathlib, sys, tarfile
directory_root, file_root, directory_content, file_content = sys.argv[1:5]
out = pathlib.Path(sys.argv[5])
manifest = {"schemaVersion": 1, "pixelVersion": "3.2.2", "paths": sorted([directory_root, file_root])}
with tarfile.open(out, mode="w:gz") as archive:
    root = tarfile.TarInfo(directory_root); root.type = tarfile.DIRTYPE; root.mode = 0o700
    root.uid = os.getuid(); root.gid = os.getgid(); archive.addfile(root)
    payload = directory_content.encode()
    member = tarfile.TarInfo(directory_root + "/state.json"); member.type = tarfile.REGTYPE
    member.mode = 0o600; member.size = len(payload); member.uid = os.getuid(); member.gid = os.getgid()
    archive.addfile(member, io.BytesIO(payload))
    payload = file_content.encode()
    member = tarfile.TarInfo(file_root); member.type = tarfile.REGTYPE
    member.mode = 0o600; member.size = len(payload); member.uid = os.getuid(); member.gid = os.getgid()
    archive.addfile(member, io.BytesIO(payload))
    payload = json.dumps(manifest).encode()
    marker = tarfile.TarInfo(".pixel-backup-manifest.json"); marker.mode = 0o600
    marker.size = len(payload); marker.uid = os.getuid(); marker.gid = os.getgid()
    archive.addfile(marker, io.BytesIO(payload))
PY
  recipient="age1testrecipient"
  printf 'PIXEL-FAKE-AGE\nRECIPIENT:%s\n' "$recipient" > "$out_path"
  cat "$tmp/payload-mixed.tar.gz" >> "$out_path"
  ssh-keygen -q -Y sign -f "$key" -n pixel-private-backup "$out_path" >/dev/null
  sha256sum "$out_path" | awk '{print $1}' > "$out_path.sha256"
}
make_file_root_backup() { # out_path relative content
  local out_path="$1" relative="$2" content="$3" key="$tmp/backup-signing-key"
  python3 - "$relative" "$content" "$tmp/payload-file.tar.gz" <<'PY'
import json, io, os, pathlib, sys, tarfile
relative = sys.argv[1]; content = sys.argv[2]; out = pathlib.Path(sys.argv[3])
manifest = {"schemaVersion": 1, "pixelVersion": "3.2.2", "paths": [relative]}
payload = content.encode()
info = tarfile.TarInfo(relative); info.type = tarfile.REGTYPE; info.mode = 0o600; info.size = len(payload)
info.uid = os.getuid(); info.gid = os.getgid()
marker = tarfile.TarInfo(".pixel-backup-manifest.json")
mpayload = json.dumps(manifest).encode(); marker.mode = 0o600; marker.size = len(mpayload)
marker.uid = os.getuid(); marker.gid = os.getgid()
with tarfile.open(out, mode="w:gz") as archive:
    archive.addfile(info, io.BytesIO(payload))
    archive.addfile(marker, io.BytesIO(mpayload))
PY
  recipient="age1testrecipient"
  printf 'PIXEL-FAKE-AGE\nRECIPIENT:%s\n' "$recipient" > "$out_path"
  cat "$tmp/payload-file.tar.gz" >> "$out_path"
  ssh-keygen -q -Y sign -f "$key" -n pixel-private-backup "$out_path" >/dev/null
  sha256sum "$out_path" | awk '{print $1}' > "$out_path.sha256"
}
# Generate the backup signing key and allowed-signers once.
key="$tmp/backup-signing-key"
ssh-keygen -q -t ed25519 -N '' -C pixel-private-backup -f "$key" >/dev/null
pubkey=$(ssh-keygen -y -f "$key")
printf 'pixel-backup %s\n' "$pubkey" > "$tmp/allowed-signers"
chmod 600 "$tmp/allowed-signers"
# A synthetic standard-restore rejection backup declares a root outside the 4.3 allowlist.
make_backup "$tmp/bad-backup.tar.gz.age" "var/lib/pixel-escape-not-in-allowlist" "X"
# The authenticated migration backup declares the temp-only migration root.
make_backup "$tmp/backup.tar.gz.age" "$relative" "NEW-LIVE-STATE"
backup="$tmp/backup.tar.gz.age"
printf '%s\n' age1testrecipient > "$tmp/identity"; chmod 600 "$tmp/identity"
contract_sha="$(printf '%s\n' "$relative" | sha256sum | awk '{print $1}')"
export PIXEL_MIGRATION_CONTRACT_SHA256="$contract_sha"
export PIXEL_BACKUP_AGE_RECIPIENT=age1testrecipient

# Custody copy with the fixed production custody root replaced by a temp root.
custody="$tmp/custody"; mkdir -p "$custody"; chmod 700 "$custody"
script="$repo/scripts/restore-private-state-custody.sh"
sed "s|/var/lib/pixel-migration-journals|$custody|g" "$repo/scripts/restore-private-state.sh" > "$script"
chmod 700 "$script"

export PATH="$fake:$PATH"
export PIXEL_SYSTEMCTL_BIN="$fake/systemctl"
export PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG="$tmp/systemctl.log"
export PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE="$tmp/systemctl.state"
export PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED="$tmp/systemctl.enabled"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED"

fail() { echo "legacy-clean-migration swap: $*" >&2; exit 1; }

run_swap() { # journal receipt [extra args...] -> sets rc, stderr
  local journal="$1" receipt="$2"; shift 2
  rc=0; stderr=''
  set +e
  (cd "$repo" && bash "$script" "$backup" --identity "$tmp/identity" \
    --signers "$tmp/allowed-signers" --replace --confirm --receipt "$receipt" \
    --migration "$journal" "$@" >/dev/null 2>"$tmp/swap-err")
  rc=$?
  set -e
  stderr=$(cat "$tmp/swap-err")
}

# --------------------------------------------------------------------------- #
# Synthetic standard-restore rejection: a non-migration restore must reject the
# migration-only root (proof by execution, not by a static grep).
# --------------------------------------------------------------------------- #
if (cd "$repo" && bash "$script" "$tmp/bad-backup.tar.gz.age" --identity "$tmp/identity" \
    --signers "$tmp/allowed-signers" --validate-only >/dev/null 2>&1); then
  fail "ordinary 4.3 restore accepted a root outside its strict allowlist"
fi

# --------------------------------------------------------------------------- #
# Real legacy-controller layout: the signed 3.2 backup includes the old controller's
# owner-private .env, while restore runs from the new 4.3 controller at a different root.
# The unchanged legacy file is preserved as an exact no-op and is never admitted to the
# privileged swap journal; the ordinary allowed state root still executes a real swap.
# --------------------------------------------------------------------------- #
legacy_env="$tmp/legacy-controller/.env"; legacy_relative="${legacy_env#/}"
mkdir -p "$(dirname "$legacy_env")"
printf 'LEGACY-ENV\n' > "$legacy_env"; chmod 600 "$legacy_env"
make_backup_with_noop_file "$tmp/legacy-env-backup.tar.gz.age" "$relative" "$legacy_relative" \
  $'NEW-LIVE-STATE\n' $'LEGACY-ENV\n' "$key"
legacy_contract=$(printf '%s\n%s\n' "$legacy_relative" "$relative" | sort -u | sha256sum | awk '{print $1}')
backup="$tmp/legacy-env-backup.tar.gz.age"
export PIXEL_MIGRATION_CONTRACT_SHA256="$legacy_contract"
mkdir -p "$tmp/out"; chmod 700 "$tmp/out"
journalLegacy="$custody/txn-legacy-env.json"
receiptLegacy="$tmp/out/receipt-legacy-env.json"
run_swap "$journalLegacy" "$receiptLegacy"
[[ $rc -eq 0 ]] || fail "migration rejected unchanged legacy controller .env: $stderr"
[[ "$(cat "$dest_root/state.json")" == "NEW-LIVE-STATE" ]] || fail "legacy-env migration did not swap the allowed root"
[[ "$(cat "$legacy_env")" == "LEGACY-ENV" ]] || fail "legacy-env migration changed the preserved legacy file"
find "$(dirname "$legacy_env")" -maxdepth 1 \( -name '.pixel-restore-.env-*.new' -o -name '.pixel-restore-.env-*.old' \) | grep -q . \
  && fail "legacy-env migration admitted the no-op file to the privileged swap journal"
(cd "$repo" && bash "$script" --migration-rollback "$journalLegacy" >/dev/null)
[[ "$(cat "$dest_root/state.json")" == "OLD-LIVE-STATE" ]] || fail "legacy-env rollback did not restore the allowed root"
[[ "$(cat "$legacy_env")" == "LEGACY-ENV" ]] || fail "legacy-env rollback changed the preserved legacy file"
# The exception is equality-only: even an owner-private regular legacy file is rejected
# before capture when its bytes drift from the authenticated backup.
printf 'DRIFTED-LEGACY-ENV\n' > "$legacy_env"
journalLegacyDrift="$custody/txn-legacy-env-drift.json"
receiptLegacyDrift="$tmp/out/receipt-legacy-env-drift.json"
run_swap "$journalLegacyDrift" "$receiptLegacyDrift"
[[ $rc -ne 0 ]] || fail "migration accepted a changed legacy controller .env as a no-op"
grep -Fq "outside the local restore contract" <<<"$stderr" \
  || fail "changed legacy controller .env did not report the destination contract"
[[ ! -e "$journalLegacyDrift" ]] || fail "changed legacy controller .env left a reserved journal behind"
[[ "$(cat "$dest_root/state.json")" == "OLD-LIVE-STATE" ]] || fail "changed legacy controller .env mutated the allowed root"
printf 'LEGACY-ENV\n' > "$legacy_env"; chmod 600 "$legacy_env"
backup="$tmp/backup.tar.gz.age"
export PIXEL_MIGRATION_CONTRACT_SHA256="$contract_sha"

# --------------------------------------------------------------------------- #
# Truthful migration contract: even in migration-only mode, a declared legacy
# destination that 4.3 no longer allows (a dropped root) is rejected by the live
# destination check, never silently accepted.
# --------------------------------------------------------------------------- #
bad_contract="$(printf '%s\n' 'var/lib/pixel-escape-not-in-allowlist' | sha256sum | awk '{print $1}')"
rc=0
set +e
(cd "$repo" && PIXEL_MIGRATION_CONTRACT_SHA256="$bad_contract" bash "$script" "$tmp/bad-backup.tar.gz.age" \
  --identity "$tmp/identity" --signers "$tmp/allowed-signers" --replace --confirm \
  --receipt "$tmp/out/receipt-bad.json" --migration "$custody/txn-bad.json" >/dev/null 2>"$tmp/bad-mig-err")
rc=$?
set -e
[[ $rc -ne 0 ]] || fail "migration accepted a legacy destination outside the 4.3 allowlist"
grep -Fq "outside the local restore contract" "$tmp/bad-mig-err" \
  || fail "migration dropped-root rejection did not report the destination contract"
[[ ! -e "$custody/txn-bad.json" ]] || fail "migration dropped-root rejection left a reserved journal behind"

# --------------------------------------------------------------------------- #
# Occupied journal: a swap must refuse an already-armed journal at the exact path
# and must never overwrite it (the privileged O_EXCL reservation is authoritative,
# not a non-root occupancy test that cannot see inside the root-0700 custody).
# --------------------------------------------------------------------------- #
journalOcc="$custody/txn-occupied.json"
receiptOcc="$tmp/out/receipt-occupied.json"
printf '%s\n' '{"schemaVersion":1,"kind":"pixel-restore-migration-journal","committed":false,"cleanup":"armed","rolledBack":false,"finalization":"armed"}' > "$journalOcc"
chmod 600 "$journalOcc"
occ_before=$(sha256sum "$journalOcc" | awk '{print $1}')
run_swap "$journalOcc" "$receiptOcc"
[[ $rc -ne 0 ]] || fail "swap unexpectedly overwrote an occupied armed journal"
occ_after=$(sha256sum "$journalOcc" | awk '{print $1}')
[[ "$occ_before" == "$occ_after" ]] || fail "occupied armed journal was overwritten by the swap"

# --------------------------------------------------------------------------- #
# Reservation race: a journal reserved for a DIFFERENT contract must be refused and
# must never be reclaimed/overwritten by a swap for this contract.
# --------------------------------------------------------------------------- #
journalRace="$custody/txn-race.json"
receiptRace="$tmp/out/receipt-race.json"
race_contract="$(printf 'other' | sha256sum | awk '{print $1}')"
printf '%s\n' "{\"schemaVersion\":1,\"kind\":\"pixel-restore-migration-reserved\",\"reservationId\":\"$(python3 -c 'import secrets;print(secrets.token_hex(16))')\",\"contractSha256\":\"$race_contract\",\"sourcePixel\":\"3.2.2\",\"targetPixel\":\"4.3.27\"}" > "$journalRace"
chmod 600 "$journalRace"
race_before=$(sha256sum "$journalRace" | awk '{print $1}')
run_swap "$journalRace" "$receiptRace"
[[ $rc -ne 0 ]] || fail "swap unexpectedly claimed a reservation held for another contract"
race_after=$(sha256sum "$journalRace" | awk '{print $1}')
[[ "$race_before" == "$race_after" ]] || fail "foreign reservation was overwritten by the swap"

# --------------------------------------------------------------------------- #
# Scenario A: the full transaction journal is armed BEFORE any live rename, so a post-swap
# verification failure (which happens after arm) must be undone by the privileged helper
# rollback from the real EXIT trap: the armed journal records verified rollback evidence and
# every pre-migration byte is reinstated (never the legacy inline best-effort rollback).
# --------------------------------------------------------------------------- #
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
journalA="$custody/txn-a.json"
receiptA="$tmp/out/receipt-a.json"
mkdir -p "$tmp/out"; chmod 700 "$tmp/out"
PIXEL_MIGRATION_TEST_VERIFY=fail run_swap "$journalA" "$receiptA"
[[ $rc -ne 0 ]] || fail "swap with post-swap verification failure unexpectedly succeeded"
[[ -f "$journalA" ]] || fail "verification failure after arm did not leave the armed journal"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-journal"' "$journalA" || fail "post-arm verification failure journal is not armed"
grep -Eq '"rolledBack":[[:space:]]*true' "$journalA" || fail "post-arm verification failure was not rolled back by the privileged helper"
grep -Eq '"rollbackProgress":\[[[:space:]]*"restored"[[:space:]]*\]' "$journalA" || fail "post-arm verification failure did not record per-root rollback progress"
[[ "$(cat "$dest_root/state.json")" == "OLD-LIVE-STATE" ]] \
  || fail "privileged rollback did not reinstate the pre-migration bytes after verification failure"
find "$(dirname "$dest_root")" -maxdepth 1 \( -name '.pixel-restore-state-root-*.old' -o -name '.pixel-restore-state-root-*.new' \) | grep -q . \
  && fail "post-arm verification failure left a temporary/old sibling behind"

# --------------------------------------------------------------------------- #
# Scenario B: post-swap verification success arms the journal; the real armed
# journal supports a real post-swap rollback (bytes + service state restored).
# --------------------------------------------------------------------------- #
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
journalB="$custody/txn-b.json"
receiptB="$tmp/out/receipt-b.json"
PIXEL_MIGRATION_TEST_VERIFY=pass run_swap "$journalB" "$receiptB"
[[ $rc -eq 0 ]] || fail "successful swap failed: $stderr"
[[ -f "$journalB" ]] || fail "successful swap did not arm a journal"
grep -Eq '"committed":[[:space:]]*false' "$journalB" || fail "armed journal was not committed=false"
grep -Eq '"rollbackProgress"' "$journalB" || fail "armed journal lacks per-root rollback progress"
grep -Eq '"oldEvidence"' "$journalB" || fail "armed journal lacks old rollback evidence"
[[ "$(cat "$dest_root/state.json")" == "NEW-LIVE-STATE" ]] || fail "swap did not activate the new state"
rc=0
set +e
(cd "$repo" && bash "$script" --migration-rollback "$journalB" >/dev/null 2>"$tmp/rb-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "armed-journal rollback failed: $(cat "$tmp/rb-err")"
grep -Eq '"rolledBack":[[:space:]]*true' "$journalB" || fail "armed-journal rollback did not record evidence"
[[ "$(cat "$dest_root/state.json")" == "OLD-LIVE-STATE" ]] || fail "armed-journal rollback did not restore the pre-migration bytes"
grep -Fq "restart openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG" \
  || fail "armed-journal rollback did not restore service state"

# --------------------------------------------------------------------------- #
# Scenario C: the real armed journal supports a real post-swap commit (old state
# deleted, verified new state retained).
# --------------------------------------------------------------------------- #
journalC="$custody/txn-c.json"
receiptC="$tmp/out/receipt-c.json"
PIXEL_MIGRATION_TEST_VERIFY=pass run_swap "$journalC" "$receiptC"
[[ $rc -eq 0 ]] || fail "second successful swap failed: $stderr"
[[ "$(cat "$dest_root/state.json")" == "NEW-LIVE-STATE" ]] || fail "second swap did not activate the new state"
rc=0
set +e
(cd "$repo" && bash "$script" --migration-commit "$journalC" >/dev/null 2>"$tmp/cm-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "armed-journal commit failed: $(cat "$tmp/cm-err")"
grep -Eq '"committed":[[:space:]]*true' "$journalC" || fail "armed-journal commit did not mark committed"
grep -Eq '"cleanup":[[:space:]]*"complete"' "$journalC" || fail "armed-journal commit did not complete cleanup"
[[ "$(cat "$dest_root/state.json")" == "NEW-LIVE-STATE" ]] || fail "armed-journal commit destroyed the new live state"
find "$(dirname "$dest_root")" -maxdepth 1 -name '.pixel-restore-state-root-*.old' | grep -q . \
  && fail "armed-journal commit left old state behind"

# --------------------------------------------------------------------------- #
# Scenario D (main regression): the full transaction journal is armed BEFORE any live
# destination rename, so a hard kill / power loss in the middle of a partial multi-root swap
# leaves an armed journal with NO false rollback claim, and the real --migration-rollback
# deterministically reconstructs unswapped and half-swapped roots and cleans every temp/old
# sibling. The shell is SIGKILLed immediately after the first actual destination->old rename
# (root A moved to old, new not installed; root B untouched), so no EXIT trap runs.
# --------------------------------------------------------------------------- #
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
destB="$tmp/ops-state"; relativeB="${destB#/}"
mkdir -p "$destB"
printf 'PRE-D-A\n' > "$dest_root/state.json"
printf 'PRE-D-B\n' > "$destB/state.json"
cat > "$tmp/multi-spec.json" <<JSON
{"roots": {
  "$relative": {"state.json": "NEW-A\n"},
  "$relativeB": {"state.json": "NEW-B\n"}
}}
JSON
make_backup_multi "$tmp/multi-backup.tar.gz.age" "$tmp/multi-spec.json" "$key"
multi_contract="$(python3 - "$relative" "$relativeB" <<'PY'
import hashlib, sys
print(hashlib.sha256(("\n".join(sorted(sys.argv[1:])) + "\n").encode()).hexdigest())
PY
)"
export PIXEL_MIGRATION_CONTRACT_SHA256="$multi_contract"
journalD="$custody/txn-d.json"
receiptD="$tmp/out/receipt-d.json"
# openclaw-gateway.service is ACTIVE (and enabled) before the migration, so the captured
# prestate is active+enabled and rollback must restore it exactly (start), never leave it
# down and never blind-restart an unrelated unit.
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED"
backup="$tmp/multi-backup.tar.gz.age"
PIXEL_MIGRATION_TEST_KILL_AFTER_MV=".pixel-restore-state-root" PIXEL_MIGRATION_TEST_VERIFY=pass run_swap "$journalD" "$receiptD"
backup="$tmp/backup.tar.gz.age"
unset PIXEL_MIGRATION_TEST_KILL_AFTER_MV
[[ $rc -ne 0 ]] || fail "SIGKILL mid-swap unexpectedly returned success"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-journal"' "$journalD" || fail "SIGKILL mid-swap left the journal unarmed"
grep -Eq '"committed":[[:space:]]*false' "$journalD" || fail "SIGKILL mid-swap journal is not committed=false"
grep -Eq '"rolledBack":[[:space:]]*false' "$journalD" || fail "SIGKILL mid-swap produced a false rolledBack claim"
grep -Eq '"rollbackProgress":\[[[:space:]]*"pending"' "$journalD" || fail "SIGKILL partial swap did not leave rollback pending"
oldA=$(find "$(dirname "$dest_root")" -maxdepth 1 -name '.pixel-restore-state-root-*.old' | head -1)
newA=$(find "$(dirname "$dest_root")" -maxdepth 1 -name '.pixel-restore-state-root-*.new' | head -1)
[[ -n "$oldA" && -n "$newA" ]] || fail "SIGKILL mid-swap did not leave the root A old/new siblings"
[[ -f "$dest_root/state.json" ]] && fail "SIGKILL mid-swap left root A at its destination instead of moving it to old"
[[ "$(cat "$oldA/state.json")" == "PRE-D-A" ]] || fail "SIGKILL mid-swap did not preserve root A old bytes at the old path"
# Roots are processed in the manifest's sorted order (ops-state before state-root), so the
# earlier root B fully swapped (new installed, old preserved) while the kill caught root A
# half-swapped (destination moved to old, new not yet installed) — a genuine partial state.
[[ "$(cat "$destB/state.json")" == "NEW-B" ]] || fail "SIGKILL mid-swap did not fully swap root B"
oldB=$(find "$(dirname "$destB")" -maxdepth 1 -name '.pixel-restore-ops-state-*.old' | head -1)
[[ -n "$oldB" && "$(cat "$oldB/state.json")" == "PRE-D-B" ]] || fail "SIGKILL mid-swap did not preserve root B old bytes at the old path"
rc=0
set +e
(cd "$repo" && bash "$script" --migration-rollback "$journalD" >/dev/null 2>"$tmp/rbd-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "SIGKILL partial-swap rollback failed: $(cat "$tmp/rbd-err")"
grep -Eq '"rolledBack":[[:space:]]*true' "$journalD" || fail "SIGKILL partial-swap rollback did not record verified rollback evidence"
[[ "$(cat "$dest_root/state.json")" == "PRE-D-A" ]] || fail "SIGKILL partial-swap rollback did not restore root A old bytes"
[[ "$(cat "$destB/state.json")" == "PRE-D-B" ]] || fail "SIGKILL partial-swap rollback did not restore root B old bytes"
find "$(dirname "$dest_root")" -maxdepth 1 \( -name '.pixel-restore-state-root-*.old' -o -name '.pixel-restore-state-root-*.new' \) | grep -q . \
  && fail "SIGKILL partial-swap rollback left a root A temp/old sibling behind"
find "$(dirname "$destB")" -maxdepth 1 \( -name '.pixel-restore-ops-state-*.old' -o -name '.pixel-restore-ops-state-*.new' \) | grep -q . \
  && fail "SIGKILL partial-swap rollback left a root B temp/old sibling behind"
# Exact captured prestate restore: the originally-active+enabled gateway is started and
# enabled again (never a blind restart-all that would also restart units that were down).
grep -Fqx "openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE" \
  || fail "SIGKILL partial-swap rollback did not restore the exact active prestate"
grep -Fqx "openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED" \
  || fail "SIGKILL partial-swap rollback did not restore the exact enabled prestate"

# --------------------------------------------------------------------------- #
# Scenario E (no-swap / pre-first-rename): SIGKILL before the first live rename must also
# leave an armed journal with no false rollback claim; the real --migration-rollback then
# cleans the prepared temp and leaves the untouched destination byte-identical.
# --------------------------------------------------------------------------- #
export PIXEL_MIGRATION_CONTRACT_SHA256="$contract_sha"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
printf 'PRE-E\n' > "$dest_root/state.json"
journalE="$custody/txn-e.json"
receiptE="$tmp/out/receipt-e.json"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
PIXEL_MIGRATION_TEST_KILL_BEFORE_MV=".pixel-restore-state-root" PIXEL_MIGRATION_TEST_VERIFY=pass run_swap "$journalE" "$receiptE"
unset PIXEL_MIGRATION_TEST_KILL_BEFORE_MV
[[ $rc -ne 0 ]] || fail "pre-rename SIGKILL unexpectedly returned success"
grep -Eq '"kind":[[:space:]]*"pixel-restore-migration-journal"' "$journalE" || fail "pre-rename SIGKILL left the journal unarmed"
grep -Eq '"rolledBack":[[:space:]]*false' "$journalE" || fail "pre-rename SIGKILL produced a false rolledBack claim"
[[ "$(cat "$dest_root/state.json")" == "PRE-E" ]] || fail "pre-rename SIGKILL mutated the destination before any rename"
rc=0
set +e
(cd "$repo" && bash "$script" --migration-rollback "$journalE" >/dev/null 2>"$tmp/rbe-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "pre-rename SIGKILL rollback failed: $(cat "$tmp/rbe-err")"
grep -Eq '"rolledBack":[[:space:]]*true' "$journalE" || fail "pre-rename SIGKILL rollback did not record verified rollback evidence"
[[ "$(cat "$dest_root/state.json")" == "PRE-E" ]] || fail "pre-rename SIGKILL rollback did not leave the destination byte-identical"
find "$(dirname "$dest_root")" -maxdepth 1 \( -name '.pixel-restore-state-root-*.old' -o -name '.pixel-restore-state-root-*.new' \) | grep -q . \
  && fail "pre-rename SIGKILL rollback left a temp/old sibling behind"

# --------------------------------------------------------------------------- #
# Scenario F (regular-file backup root regression): the authenticated 3.2.2 backup root
# contract includes top-level regular files (e.g. etc/pixel-ops-broker.env), and
# restore-private-state.sh stages each root to a sibling temp preserving whether it is a
# directory or a regular file. The pre-arm validator must accept a regular single-link temp
# root, drive the real --migration swap, and let rollback restore the exact pre-migration
# bytes while commit retains the verified new bytes.
# --------------------------------------------------------------------------- #
export PIXEL_MIGRATION_CONTRACT_SHA256="$contract_sha"
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
file_root="$tmp/file-root"; relative_file="${file_root#/}"
mkdir -p "$(dirname "$file_root")"
printf 'OLD-FILE-ROOT\n' > "$file_root"
# The file root is not covered by the swap-test's standard .env allowlist, so point the
# source-broker env root at it for this scenario (only add_allowed consumes that variable)
# and restore the original .env afterwards.
cp "$repo/.env" "$tmp/env-backup"
sed -i "s|^PIXEL_SOURCE_BROKER_ENV=.*|PIXEL_SOURCE_BROKER_ENV=$file_root|" "$repo/.env"
make_file_root_backup "$tmp/file-backup.tar.gz.age" "$relative_file" $'NEW-FILE-ROOT\n'
file_contract="$(printf '%s\n' "$relative_file" | sha256sum | awk '{print $1}')"
journalF="$custody/txn-f.json"
receiptF="$tmp/out/receipt-f.json"
export PIXEL_MIGRATION_CONTRACT_SHA256="$file_contract"
backup="$tmp/file-backup.tar.gz.age"
run_swap "$journalF" "$receiptF"
[[ $rc -eq 0 ]] || fail "regular-file root swap failed: $stderr"
[[ -f "$journalF" ]] || fail "regular-file root swap did not arm a journal"
grep -Eq '"committed":[[:space:]]*false' "$journalF" || fail "regular-file root journal was not committed=false"
[[ "$(cat "$file_root")" == "NEW-FILE-ROOT" ]] || fail "regular-file root swap did not activate the new bytes"
rc=0
set +e
(cd "$repo" && bash "$script" --migration-rollback "$journalF" >/dev/null 2>"$tmp/rbf-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "regular-file root rollback failed: $(cat "$tmp/rbf-err")"
grep -Eq '"rolledBack":[[:space:]]*true' "$journalF" || fail "regular-file root rollback did not record verified rollback evidence"
[[ "$(cat "$file_root")" == "OLD-FILE-ROOT" ]] || fail "regular-file root rollback did not restore the exact pre-migration bytes"
find "$(dirname "$file_root")" -maxdepth 1 \( -name '.pixel-restore-file-root-*.old' -o -name '.pixel-restore-file-root-*.new' \) | grep -q . \
  && fail "regular-file root rollback left a temp/old sibling behind"
# The same regular-file root swap supports a real post-swap commit (new bytes retained).
journalF2="$custody/txn-f2.json"
receiptF2="$tmp/out/receipt-f2.json"
run_swap "$journalF2" "$receiptF2"
[[ $rc -eq 0 ]] || fail "regular-file root second swap failed: $stderr"
[[ "$(cat "$file_root")" == "NEW-FILE-ROOT" ]] || fail "regular-file root second swap did not activate the new bytes"
rc=0
set +e
(cd "$repo" && bash "$script" --migration-commit "$journalF2" >/dev/null 2>"$tmp/cmf-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "regular-file root commit failed: $(cat "$tmp/cmf-err")"
grep -Eq '"committed":[[:space:]]*true' "$journalF2" || fail "regular-file root commit did not mark committed"
grep -Eq '"cleanup":[[:space:]]*"complete"' "$journalF2" || fail "regular-file root commit did not complete cleanup"
[[ "$(cat "$file_root")" == "NEW-FILE-ROOT" ]] || fail "regular-file root commit destroyed the verified new bytes"
find "$(dirname "$file_root")" -maxdepth 1 -name '.pixel-restore-file-root-*.old' | grep -q . \
  && fail "regular-file root commit left old state behind"
cp "$tmp/env-backup" "$repo/.env"
backup="$tmp/backup.tar.gz.age"
export PIXEL_MIGRATION_CONTRACT_SHA256="$contract_sha"

# --------------------------------------------------------------------------- #
# Scenario G (full deployment-path activation through the OUTER WRAPPER): the real
# restore-private-state.sh is executed with a real --migration-spec + --migration-stage, so
# the production ordering reserve -> capture exact prestate -> verified quiesce ->
# backup/staging -> arm -> install/activate (current-link + config) -> finalize desired
# state -> runtime verify -> commit all run through the wrapper. Then an injected failure
# AFTER arm (post-finalize runtime-verify failure) proves the wrapper's armed rollback
# restores exact files/current-link/service-prestate.
# --------------------------------------------------------------------------- #
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
deploy_root="$tmp/deploy"
mkdir -p "$deploy_root"; chmod 700 "$deploy_root"
printf 'OLD-CONFIG\n' > "$deploy_root/app.json"; chmod 600 "$deploy_root/app.json"
ln -s "releases/3.2.2" "$deploy_root/current"
# openclaw-gateway.service active+enabled before migration (exact prestate to restore).
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED"
# Model the optional 4.3 units (courier, source timer, ops, frontier) as genuinely
# not-found on this host, exactly as a disabled optional unit can be on a real host.
: > "$tmp/deploy-notfound"
for u in pixel-web-courier.service pixel-source-broker.timer pixel-ops-broker.service pixel-frontier-broker.service; do
  printf '%s\n' "$u" >> "$tmp/deploy-notfound"
done
export PIXEL_MIGRATION_TEST_SYSTEMCTL_NOTFOUND="$tmp/deploy-notfound"
printf 'NEW-CONFIG\n' > "$tmp/deploy-config-new"
config_sha=$(sha256sum "$tmp/deploy-config-new" | awk '{print $1}')
stage_g="$tmp/deploy-stage"; mkdir -p "$stage_g"; chmod 700 "$stage_g"
ln -s "releases/4.3.27" "$stage_g/.pixel-restore-current-0-0.new"
cp "$tmp/deploy-config-new" "$stage_g/.pixel-restore-app.json-0-1.new"
specG="$tmp/deploy-spec.json"
cat > "$specG" <<JSON
{"deploymentItems":[
  {"kind":"symlink","path":"$deploy_root/current","oldPath":"$deploy_root/.pixel-restore-current-0-0.old","newPath":"$deploy_root/.pixel-restore-current-0-0.new","hadOld":1,"target":"releases/4.3.27"},
  {"kind":"config","path":"$deploy_root/app.json","oldPath":"$deploy_root/.pixel-restore-app.json-0-1.old","newPath":"$deploy_root/.pixel-restore-app.json-0-1.new","hadOld":1,"sha256":"$config_sha"}
],
"serviceDesired":{
  "openclaw-gateway.service":{"enabled":true,"active":true},
  "pixel-web-courier.service":{"enabled":false,"active":false},
  "pixel-source-broker.timer":{"enabled":false,"active":false},
  "pixel-ops-broker.service":{"enabled":false,"active":false},
  "pixel-frontier-broker.service":{"enabled":false,"active":false}}}
JSON
chmod 600 "$specG"
journalG="$custody/txn-g.json"
receiptG="$tmp/out/receipt-g.json"
PIXEL_MIGRATION_TEST_VERIFY=pass run_swap "$journalG" "$receiptG" --migration-spec "$specG" --migration-stage "$stage_g"
[[ $rc -eq 0 ]] || fail "full deployment-path activation failed: $stderr"
[[ -f "$journalG" ]] || fail "deployment-path activation did not arm a journal"
grep -Eq '"committed":[[:space:]]*false' "$journalG" || fail "deployment-path journal was not committed=false"
# Requirement 2: the affected-unit set always includes ALL fixed units regardless of the
# desired enabled/active state (gateway, courier, source timer, ops, frontier).
for fixed_unit in openclaw-gateway.service pixel-web-courier.service pixel-source-broker.timer pixel-ops-broker.service pixel-frontier-broker.service; do
  grep -Fq "$fixed_unit" "$journalG" || fail "migration affected-unit set omitted fixed unit: $fixed_unit"
done
# install/activate applied the current link + config.
[[ "$(readlink "$deploy_root/current")" == "releases/4.3.27" ]] || fail "deployment-path activation did not switch the current link"
[[ "$(cat "$deploy_root/app.json")" == "NEW-CONFIG" ]] || fail "deployment-path activation did not apply the config candidate"
# finalize applied the exact desired state (gateway active+enabled) through the wrapper.
grep -Fqx "openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE" \
  || fail "deployment-path activation did not finalize the desired active state"
grep -Fqx "openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED" \
  || fail "deployment-path activation did not finalize the desired enabled state"
# Requirement 5: the optional false/false units remain absent/inactive/disabled (never
# enabled/started) - they are not-found and must be left alone.
for optional in pixel-web-courier.service pixel-source-broker.timer pixel-ops-broker.service pixel-frontier-broker.service; do
  grep -Fqx "$optional" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"     && fail "deployment-path finalize activated an optional not-found unit: $optional"
  grep -Fqx "$optional" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED"     && fail "deployment-path finalize enabled an optional not-found unit: $optional"
done
# Requirement 8: a migration NEVER runs the legacy pixel-work-* wildcard stop after the
# helper's exact quiescence (the deployment lock prevents new work launches; the wildcard
# could mutate a unit not in the captured prestate). Deep work is enabled in this harness.
if grep -Eq 'stop pixel-work-\*' "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"; then
  fail "migration invoked the legacy pixel-work-* wildcard stop"
fi
# commit retains the verified new state.
rc=0; set +e
(cd "$repo" && bash "$script" --migration-commit "$journalG" >/dev/null 2>"$tmp/cmg-err"); rc=$?
set -e
[[ $rc -eq 0 ]] || fail "deployment-path commit failed: $(cat "$tmp/cmg-err")"
grep -Eq '"committed":[[:space:]]*true' "$journalG" || fail "deployment-path commit did not mark committed"

# Injected failure AFTER arm: the runtime-verify fails after finalize, so the armed journal
# rollback must restore exact files/current-link/service-prestate.
: > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"
printf 'OLD-CONFIG\n' > "$deploy_root/app.json"; chmod 600 "$deploy_root/app.json"
rm -f "$deploy_root/current"; ln -s "releases/3.2.2" "$deploy_root/current"
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"
printf '%s\n' "openclaw-gateway.service" > "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED"
journalG2="$custody/txn-g2.json"
receiptG2="$tmp/out/receipt-g2.json"
PIXEL_MIGRATION_TEST_VERIFY=fail run_swap "$journalG2" "$receiptG2" --migration-spec "$specG" --migration-stage "$stage_g"
[[ $rc -ne 0 ]] || fail "post-arm verification failure unexpectedly succeeded"
grep -Eq '"rolledBack":[[:space:]]*true' "$journalG2" || fail "post-arm verification failure did not roll back through the armed journal"
[[ "$(readlink "$deploy_root/current")" == "releases/3.2.2" ]] || fail "post-arm rollback did not restore the exact current link"
[[ "$(cat "$deploy_root/app.json")" == "OLD-CONFIG" ]] || fail "post-arm rollback did not restore the exact config bytes"
grep -Fqx "openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE" \
  || fail "post-arm rollback did not restore the exact active service prestate"
grep -Fqx "openclaw-gateway.service" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED" \
  || fail "post-arm rollback did not restore the exact enabled service prestate"
# Post-finalize rollback restores ALL fixed service prestate: the optional not-found units
# are never restarted or enabled, and the migration never ran the wildcard stop.
for optional in pixel-web-courier.service pixel-source-broker.timer pixel-ops-broker.service pixel-frontier-broker.service; do
  grep -Fqx "$optional" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"     && fail "post-arm rollback activated an optional not-found unit: $optional"
  grep -Fqx "$optional" "$PIXEL_MIGRATION_TEST_SYSTEMCTL_ENABLED"     && fail "post-arm rollback enabled an optional not-found unit: $optional"
done
if grep -Eq 'stop pixel-work-\*' "$PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"; then
  fail "migration invoked the legacy pixel-work-* wildcard stop during rollback"
fi
unset PIXEL_MIGRATION_TEST_SYSTEMCTL_NOTFOUND

# --------------------------------------------------------------------------- #
# Successful ordinary (non-migration) validate/rehearse/restore path (regression
# #3): the real cleanup trap and `set -u` state must let a clean success exit
# without error, not only a rejection path that happens to mask a broken trap.
# --------------------------------------------------------------------------- #
if ! (cd "$repo" && bash "$script" "$backup" --identity "$tmp/identity" \
    --signers "$tmp/allowed-signers" --validate-only >/dev/null 2>"$tmp/ord-val-err"); then
  fail "ordinary validate-only did not exit cleanly: $(cat "$tmp/ord-val-err")"
fi
rehearse_root="$tmp/rehearse-root"
if ! (cd "$repo" && bash "$script" "$backup" --identity "$tmp/identity" \
    --signers "$tmp/allowed-signers" --rehearse "$rehearse_root" >/dev/null 2>"$tmp/ord-rh-err"); then
  fail "ordinary rehearse did not exit cleanly: $(cat "$tmp/ord-rh-err")"
fi
[[ -f "$rehearse_root/$relative/state.json" ]] || fail "ordinary rehearse did not stage the restored content"
if ! (cd "$repo" && bash "$script" "$backup" --identity "$tmp/identity" \
    --signers "$tmp/allowed-signers" --replace --confirm >/dev/null 2>"$tmp/ord-restore-err"); then
  fail "ordinary restore did not exit cleanly: $(cat "$tmp/ord-restore-err")"
fi
[[ "$(cat "$dest_root/state.json")" == "NEW-LIVE-STATE" ]] || fail "ordinary restore did not activate the new state"

echo "legacy-clean-migration isolated swap transaction behaved faithfully"
