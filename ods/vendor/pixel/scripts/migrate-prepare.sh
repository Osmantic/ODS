#!/usr/bin/env bash
# Migration-only prepare phase (non-live).
#
# Builds the EXACT target release directory and a strict deployment spec that the migration
# activation later consumes, WITHOUT mutating any live managed path, service state,
# enablement, or the current pointer, and WITHOUT calling apply.sh against live state.
#
# The prepared release is built through the SAME shared release-build primitive as
# scripts/apply.sh (scripts/lib/release-build.sh), so the migration release is
# byte-contract-equivalent to what ordinary apply would install. Every reviewed plan
# artifact apply.sh requires is required and verified here, and the config is rerendered
# against the intended live $PIXEL_INSTALL_DIR/current paths and required byte-equal - there
# is no unreviewed fallback render.
#
# The deployment spec covers every apply-managed live state item (current pointer, config,
# gateway/courier env, gateway/courier unit, managed workspace files). Candidate content is
# staged under the explicit staging root keyed by each item's newPath basename with an exact
# content digest; activation verifies the staged bundle against the prepare receipt before
# any live mutation. The spec/receipt are written by a strict structured writer (never
# string-built JSON), and unit-file staging is bounded to the exact configured unit
# dirs/names - no arbitrary --unit-parent authority.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/release-build.sh
source "$ROOT/scripts/lib/release-build.sh"
pixel_load_env

# Release-version binding: derive the exact migration target from the COHERENT in-tree
# release identity ($ROOT/VERSION == RELEASE-MANIFEST pixel == legacy target). This is a
# READ-ONLY coherence check only; it does NOT authenticate the source or release tree. No
# environment/CLI override, basename inference, loose semver, or fallback: an incoherent
# field/source substitution fails closed, while a coherent future signed release identity is
# accepted only as a release identity. Authenticity/custody come from the qualified-source,
# release-tree, and prepare gates below - never from these agreeing raw files. It runs here,
# before argument parsing, only because it is read-only: no live state is mutated until after
# the reviewed-plan, qualified-source, release-tree, and prepare gates later in this script.
TARGET_PIXEL=$(python3 -c '
import json, os, re, stat, sys
version_re = re.compile(r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
MAX_VERSION = 64
MAX_MANIFEST = 1024 * 1024
root = sys.argv[1]

def identity(st):
    return (st.st_dev, st.st_ino, stat.S_ISREG(st.st_mode), st.st_nlink,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)

def read_release(name, maximum):
    if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_CLOEXEC")):
        sys.exit("platform lacks O_NOFOLLOW/O_CLOEXEC release-identity guarantees")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    path = os.path.join(root, name)
    try:
        fd = os.open(path, flags)
    except OSError:
        sys.exit(f"release {name} is unavailable or unsafe")
    try:
        pre = os.fstat(fd)
        if not stat.S_ISREG(pre.st_mode) or pre.st_nlink != 1 or not 0 <= pre.st_size <= maximum:
            sys.exit(f"release {name} is not a bounded single-link regular file")
        payload = bytearray()
        while len(payload) < pre.st_size:
            chunk = os.read(fd, pre.st_size - len(payload))
            if not chunk:
                sys.exit(f"release {name} ended early during read")
            payload += chunk
        if os.read(fd, 1):
            sys.exit(f"release {name} grew during read")
        post = os.fstat(fd)
        if identity(pre) != identity(post):
            sys.exit(f"release {name} changed during read")
        current = os.lstat(path)
        if identity(post) != identity(current):
            sys.exit(f"release {name} changed during read")
        return bytes(payload)
    finally:
        os.close(fd)

def parse_manifest(payload):
    def reject_duplicates(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                sys.exit("release manifest contains duplicate fields")
            value[key] = child
        return value
    return json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates,
                      parse_constant=lambda _v: (_ for _ in ()).throw(
                          SystemExit("release manifest contains a non-finite number")))

try:
    version = read_release("VERSION", MAX_VERSION).decode("ascii").strip()
except (OSError, UnicodeDecodeError):
    sys.exit("release VERSION is unavailable or not ascii")
if version_re.fullmatch(version) is None:
    sys.exit("release VERSION is malformed")
try:
    manifest = parse_manifest(read_release("RELEASE-MANIFEST.json", MAX_MANIFEST))
except (OSError, ValueError, UnicodeDecodeError):
    sys.exit("release manifest is unavailable or invalid")
legacy = manifest.get("legacyCleanMigration")
v1 = legacy.get("v1Contract") if isinstance(legacy, dict) else None
if not isinstance(v1, dict) or v1.get("sourcePixel") != "3.2.2":
    sys.exit("release manifest legacy source does not match the contract")
if not isinstance(v1.get("targetPixel"), str) \
        or version_re.fullmatch(v1["targetPixel"]) is None:
    sys.exit("release manifest legacy target is malformed")
if manifest.get("pixel") != version or v1["targetPixel"] != version:
    sys.exit("release manifest target does not match VERSION")
print(v1["targetPixel"])
' "$ROOT") || pixel_die "migration prepare could not bind the exact release target"

usage() {
  echo "Usage: migrate-prepare.sh --install-dir LIVE_INSTALL_DIR [--workspace LIVE_WORKSPACE]" >&2
  echo "                            [--agent-env-dir LIVE_AGENT_ENV_DIR]" >&2
  echo "                            [--openclaw-home DIR] --stage STAGE_DIR --spec SPEC_OUT.json --receipt RECEIPT_OUT.json" >&2
  exit 2
}

install_dir=''; workspace=''; agent_env_dir=''; openclaw_home=''; stage=''; spec=''; receipt=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) [[ $# -ge 2 ]] || usage; install_dir=$2; shift 2 ;;
    --workspace) [[ $# -ge 2 ]] || usage; workspace=$2; shift 2 ;;
    --agent-env-dir) [[ $# -ge 2 ]] || usage; agent_env_dir=$2; shift 2 ;;
    --openclaw-home) [[ $# -ge 2 ]] || usage; openclaw_home=$2; shift 2 ;;
    --stage) [[ $# -ge 2 ]] || usage; stage=$2; shift 2 ;;
    --spec) [[ $# -ge 2 ]] || usage; spec=$2; shift 2 ;;
    --receipt) [[ $# -ge 2 ]] || usage; receipt=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$install_dir" && -n "$stage" && -n "$spec" && -n "$receipt" ]] || usage
workspace=${workspace:-${PIXEL_WORKSPACE:?}}
agent_env_dir=${agent_env_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent}
openclaw_home=${openclaw_home:-${OPENCLAW_HOME:-$HOME/.openclaw}}

install_dir=$(realpath "$install_dir")
workspace=$(realpath "$workspace")
agent_env_dir=$(realpath "$agent_env_dir")
openclaw_home=$(realpath "$openclaw_home")
stage=$(realpath -m "$stage")
spec=$(realpath -m "$spec")
receipt=$(realpath -m "$receipt")
[[ "$stage" != "$install_dir" && "$stage" != "$install_dir"/* && "$install_dir" != "$stage"/* ]] \
  || pixel_die "prepare stage must be outside the live install directory"
for p in "$stage" "$spec" "$receipt"; do
  [[ "$p" == /* && "$p" != "/" ]] || pixel_die "prepare stage/spec/receipt must be absolute non-root paths"
done
[[ -d "$stage" ]] || pixel_die "prepare stage directory does not exist: $stage"
[[ -f "$spec" ]] && pixel_die "prepare spec output already exists; refuse to overwrite: $spec"
[[ -f "$receipt" ]] && pixel_die "prepare receipt output already exists; refuse to overwrite: $receipt"

# The gateway and courier unit directories are SEPARATE product settings read from the
# reviewed configure.mjs contract (PIXEL_GATEWAY_SYSTEMD_DIR vs PIXEL_COURIER_SYSTEMD_DIR).
# There is no runtime environment override anywhere in the production path and never a
# single unit_parent pretending to represent both. Each must resolve to a configured
# supported systemd dir from the same reviewed contract (in production that is the fixed
# /etc/systemd/system). Tests mechanically patch configure.mjs in a copied repo so the
# supported dirs list carries the harness parent; no environment variable ever broadens the
# production unit-path contract.
mapfile -t SUPPORTED_UNIT_DIRS < <(python3 - "$ROOT/scripts/configure.mjs" <<'PY'
import re, sys
src = open(sys.argv[1], encoding='utf-8').read()
keys = ("PIXEL_GATEWAY_SYSTEMD_DIR", "PIXEL_COURIER_SYSTEMD_DIR", "PIXEL_SOURCE_BROKER_SYSTEMD_DIR",
        "PIXEL_OPS_BROKER_SYSTEMD_DIR", "PIXEL_FRONTIER_BROKER_SYSTEMD_DIR")
print("\n".join(sorted({m.group(1) for key in keys
                        if (m := re.search(re.escape(key) + r':\s*"([^"]+)"', src)) is not None})))
PY
)
read_unit_dir() {
  local key=$1
  python3 - "$ROOT/scripts/configure.mjs" "$key" <<'PY'
import re, sys
src = open(sys.argv[1], encoding='utf-8').read()
key = sys.argv[2]
m = re.search(re.escape(key) + r':\s*"([^"]+)"', src)
print(m.group(1) if m else "")
PY
}
gw_unit_dir=$(read_unit_dir PIXEL_GATEWAY_SYSTEMD_DIR)
courier_unit_dir=$(read_unit_dir PIXEL_COURIER_SYSTEMD_DIR)
for label_dir in "gateway:$gw_unit_dir" "courier:$courier_unit_dir"; do
  label=${label_dir%%:*}; d=${label_dir#*:}
  [[ -n "$d" && "$d" == /* && "$d" != "/" ]] || pixel_die "$label unit dir is not a configured supported systemd dir"
  is_supported=0
  for sd in "${SUPPORTED_UNIT_DIRS[@]}"; do [[ "$d" == "$sd" ]] && is_supported=1; done
  [[ $is_supported == 1 ]] || pixel_die "$label unit dir is not a configured supported systemd dir: $d"
done

# Requirement 7: bind the LOADED actual unit directories to the authored values. The
# migration stages gateway/courier unit files into the actual loaded
# PIXEL_GATEWAY_SYSTEMD_DIR / PIXEL_COURIER_SYSTEMD_DIR (defaulting to the authored value
# when the env is absent). If the loaded actual value drifts from the authored production
# contract, prepare fails BEFORE any release build - a mismatched unit dir must never be
# silently adopted for deployment artifacts.
loaded_gw_unit_dir=${PIXEL_GATEWAY_SYSTEMD_DIR:-$gw_unit_dir}
loaded_courier_unit_dir=${PIXEL_COURIER_SYSTEMD_DIR:-$courier_unit_dir}
[[ "$loaded_gw_unit_dir" == "$gw_unit_dir" ]] || pixel_die "migration prepare rejects gateway unit dir mismatch: loaded $loaded_gw_unit_dir (expected authored $gw_unit_dir)"
[[ "$loaded_courier_unit_dir" == "$courier_unit_dir" ]] || pixel_die "migration prepare rejects courier unit dir mismatch: loaded $loaded_courier_unit_dir (expected authored $courier_unit_dir)"

# Requirement 5: bind the actual deployment-config unit NAMES to the authored production
# names. A custom PIXEL_*_UNIT / PIXEL_*_TIMER value is never silently adopted for the
# migration deployment artifacts; it fails early (before any release build) unless it equals
# the authored production name.
for pair in "gateway:${PIXEL_SYSTEMD_UNIT:-openclaw-gateway.service}:openclaw-gateway.service" \
            "courier:${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}:pixel-web-courier.service" \
            "source-timer:${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}:pixel-source-broker.timer" \
            "ops:${PIXEL_OPS_BROKER_UNIT:-pixel-ops-broker.service}:pixel-ops-broker.service" \
            "frontier:${PIXEL_FRONTIER_BROKER_UNIT:-pixel-frontier-broker.service}:pixel-frontier-broker.service"; do
  label=${pair%%:*}; rest=${pair#*:}; actual=${rest%%:*}; authored=${rest#*:}
  [[ "$actual" == "$authored" ]] || pixel_die "migration prepare rejects custom unit name for $label: $actual (expected $authored)"
done

# Prove the legacy current pointer is an exact, safe, non-traversal symlink to the 3.2.2
# release BEFORE the writer binds it as hadOld=1. The migration never starts from an
# ambiguous/arbitrary current pointer.
[[ -L "$install_dir/current" ]] || pixel_die "migration prepare requires the live current pointer to be a symlink"
current_target=$(readlink "$install_dir/current")
[[ "$current_target" == "releases/3.2.2" ]] || pixel_die "migration prepare requires current to be an exact safe symlink to releases/3.2.2, not: $current_target"

# --- Reviewed plan artifacts (identical requirement to apply.sh) ---
candidate="$ROOT/dist/openclaw.json"
hash_file="$ROOT/dist/openclaw.sha256"
deployment_hash_file="$ROOT/dist/deployment.sha256"
release_identity_file="$ROOT/dist/release-identity.json"
source_runtime_file="$ROOT/dist/source-runtime.sha256"
[[ -f "$candidate" && -f "$hash_file" && -f "$deployment_hash_file" && -f "$release_identity_file" && -f "$source_runtime_file" ]] \
  || pixel_die "No reviewed plan. Run ./pixel plan first (dist artifacts missing)."
(cd "$ROOT/dist" && sha256sum -c "$(basename "$hash_file")") >/dev/null || pixel_die "Reviewed candidate changed; run ./pixel plan again"
(cd "$ROOT" && sha256sum -c "$deployment_hash_file") >/dev/null || pixel_die "Deployment inputs changed after planning; run ./pixel plan again"
check=$(mktemp)
trap 'rm -f -- "$check"' EXIT
# Rerender against the intended LIVE install paths (never the staging release), then require
# byte equality with the reviewed candidate. There is no unreviewed fallback render.
export PIXEL_PLUGIN_PATH="$install_dir/current/plugin"
export PIXEL_OPS_PLUGIN_PATH="$install_dir/current/plugin-ops"
export PIXEL_FRONTIER_PLUGIN_PATH="$install_dir/current/plugin-frontier"
node "$ROOT/scripts/render-config.mjs" "$check" >/dev/null
cmp -s "$candidate" "$check" || pixel_die "Migration config is not byte-equal to the reviewed plan at live paths; run ./pixel plan again"

# .generated plan outputs required to stage exact env/unit/workspace candidates.
generated="$ROOT/.generated"
for required in gateway.env openclaw-gateway.service web-courier.env pixel-web-courier.service \
                workspace/WEB-NAVIGATION.md workspace/scripts/browse.sh workspace/scripts/research-ledger.py; do
  [[ -f "$generated/$required" ]] || pixel_die "missing reviewed generated plan artifact .generated/$required (run ./pixel plan)"
done

# === Bounded versioned-release preinstall subtransaction (finding 2) ===
# The target release tree is an immutable, unreferenced version under
# $PIXEL_INSTALL_DIR/releases - NOT active live state (current stays on 3.2.2 until
# activation). It is prepared atomically here so a later activation never needs a generic
# recursive privileged directory mover.
pixel_acquire_deployment_lock exclusive

fsync_dir() {
  python3 - "$1" <<'PY' || pixel_die "fsync failed: $1"
import os, sys
fd = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

fsync_tree() {
  local root=$1
  # fsync every regular file and directory under the built tree so the atomic rename below
  # is durable across power loss.
  while IFS= read -r -d '' p; do
    if [[ -d "$p" && ! -L "$p" ]]; then
      fsync_dir "$p"
    elif [[ -f "$p" && ! -L "$p" ]]; then
      python3 - "$p" <<'PY' || pixel_die "fsync failed: $p"
import os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
    fi
  done < <(find "$root" -print0)
}

releases_dir="$install_dir/releases"
release="$releases_dir/$TARGET_PIXEL"
[[ -d "$releases_dir" ]] || install -d -m 700 "$releases_dir"

# Prove install dir + releases are owner-private, non-symlink, and share the same supported
# deployment owner as the invoking user. Never follow a symlinked install/releases path.
for d in "$install_dir" "$releases_dir"; do
  [[ -d "$d" && ! -L "$d" ]] || pixel_die "migration prepare requires a non-symlink directory: $d"
  local_mode=$(stat -c '%a' "$d")
  [[ "$local_mode" == "700" || "$local_mode" == "0700" ]] || pixel_die "migration prepare requires owner-private mode 700: $d"
done
install_uid=$(stat -c '%u' "$install_dir"); install_gid=$(stat -c '%g' "$install_dir")
releases_uid=$(stat -c '%u' "$releases_dir"); releases_gid=$(stat -c '%g' "$releases_dir")
[[ "$install_uid" == "$releases_uid" && "$install_gid" == "$releases_gid" ]] \
  || pixel_die "migration prepare requires install dir and releases to share one owner"
[[ "$(id -u)" == "$install_uid" && "$(id -g)" == "$install_gid" ]] \
  || pixel_die "migration prepare must run as the deployment owner of the install dir"

hidden="$releases_dir/.${TARGET_PIXEL}.prepare.$$"
cleanup_hidden() { [[ -z "$hidden" ]] || { [[ -d "$hidden" && ! -L "$hidden" ]] && rm -rf -- "$hidden"; }; }
trap 'cleanup_hidden; rm -f -- "$check"' EXIT

# Crash-left unique hidden siblings (.$TARGET_PIXEL.prepare.<pid>) are left inert and ignored by a
# later unique prepare; only the EXACT hidden path this process created is removed by
# cleanup_hidden above. Unknown same-pattern stale siblings are never auto-deleted (filename
# + owner is not exact evidence); a separately reviewed cleanup/inspection path owns them.

# Build through the shared exact primitive into a unique hidden sibling on the same
# filesystem (same dev as releases), then fsync + verify before the atomic rename.
pixel_build_release_stage "$ROOT" "$hidden"
fsync_tree "$hidden"

# Verify the exact immutable version artifacts before the tree becomes visible.
(cd "$hidden" && sha256sum -c install-manifest.sha256) >/dev/null || pixel_die "release install-manifest verification failed"
grep -qx "$TARGET_PIXEL" "$hidden/VERSION" || pixel_die "release VERSION does not match target $TARGET_PIXEL"
for artifact in release-identity.json deployment-inputs.sha256 source-runtime.sha256; do
  [[ -s "$hidden/$artifact" ]] || pixel_die "release artifact missing: $artifact"
done

# Canonical complete-tree digest of the freshly built hidden release (shared helper). This
# is the expected digest that both the existing-release adoption check and the receipt
# generation must match, so a drifted/reused release always fails closed.
hidden_tree_sha=$(pixel_release_tree_sha "$hidden")
if [[ -e "$release" ]]; then
  # Idempotency: an exact already-installed target release is a safe no-op; a non-exact
  # pre-existing target release is rejected. Adoption requires BOTH the per-file manifest
  # checks AND an exact canonical complete-tree digest equality against the freshly built
  # hidden release (types, paths, modes, regular bytes/hash/size, symlink target strings),
  # so an extra dir/symlink, a changed symlink target, or a changed mode can never be
  # silently adopted and self-bound into a new prepare receipt.
  if [[ -d "$release" && ! -L "$release" ]] \
     && (cd "$release" && sha256sum -c install-manifest.sha256) >/dev/null 2>&1 \
     && cmp -s "$release/install-manifest.sha256" "$hidden/install-manifest.sha256" \
     && [[ "$(pixel_release_tree_sha "$release")" == "$hidden_tree_sha" ]]; then
    rm -rf -- "$hidden"
  else
    rm -rf -- "$hidden"
    pixel_die "migration prepare: a non-exact $TARGET_PIXEL release already exists: $release"
  fi
else
  fsync_dir "$releases_dir"
  mv -f -- "$hidden" "$release"
  fsync_dir "$releases_dir"
fi
hidden=""

# --- Structured writer: derive exact sibling paths, stage candidates, emit spec + receipt ---
python3 - "$spec" "$receipt" "$stage" "$TARGET_PIXEL" "$install_dir" "$openclaw_home" \
        "$agent_env_dir" "$gw_unit_dir" "$courier_unit_dir" "$workspace" "$candidate" \
        "$generated" \
        "${PIXEL_SYSTEMD_UNIT:-openclaw-gateway.service}" \
        "${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}" \
        "${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}" \
        "${PIXEL_OPS_BROKER_UNIT:-pixel-ops-broker.service}" \
        "${PIXEL_FRONTIER_BROKER_UNIT:-pixel-frontier-broker.service}" \
        "${PIXEL_WEB_COURIER_ENABLED:-1}" "${PIXEL_LIMB_WEB_ENABLED:-1}" \
        "${PIXEL_SOURCE_BROKER_ENABLED:-0}" "${PIXEL_OPS_BROKER_ENABLED:-0}" \
        "${PIXEL_FRONTIER_BROKER_ENABLED:-0}" "$ROOT" "$hidden_tree_sha" <<'PY'
import hashlib, json, os, stat, subprocess, sys

(spec_out, receipt_out, stage, target_pixel, install_dir, openclaw_home, agent_env_dir,
 gw_unit_dir, courier_unit_dir, workspace, candidate, generated, gw_unit, courier_unit,
 src_timer, ops_unit, frontier_unit, courier_enabled, limb_web_enabled, source_enabled,
 ops_enabled, frontier_enabled, root, expected_tree_sha) = sys.argv[1:]
# Binding (finding/requirement 5): the migration deployment contract must use the exact
# authored production unit names. A custom unit-name env value is never silently adopted
# for deployment artifacts; it fails early unless it equals the authored production name.
for label, actual, authored in (
        ("gateway", gw_unit, "openclaw-gateway.service"),
        ("courier", courier_unit, "pixel-web-courier.service"),
        ("source timer", src_timer, "pixel-source-broker.timer"),
        ("ops", ops_unit, "pixel-ops-broker.service"),
        ("frontier", frontier_unit, "pixel-frontier-broker.service")):
    if actual != authored:
        sys.exit(f"prepare: deployment contract unit name mismatch for {label}: {actual!r} (expected {authored!r})")
courier_enabled = courier_enabled == "1"
limb_web_enabled = limb_web_enabled == "1"
source_enabled = source_enabled == "1"
ops_enabled = ops_enabled == "1"
frontier_enabled = frontier_enabled == "1"
# Strict reviewed serviceDesired transaction contract (finding 5): gateway always
# enabled+active; courier enabled+active iff the target courier is enabled (else disabled+inactive even
# if previously installed/running); source timer/ops/frontier from the reviewed target flags.
# Dynamic deep-work units are NOT listed here and preserve their captured prestate at runtime.
service_desired = {
    gw_unit: {"enabled": True, "active": True},
    courier_unit: {"enabled": courier_enabled, "active": courier_enabled},
    src_timer: {"enabled": source_enabled, "active": source_enabled},
    ops_unit: {"enabled": ops_enabled, "active": ops_enabled},
    frontier_unit: {"enabled": frontier_enabled, "active": frontier_enabled},
}
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

# --- Descriptor-bound stage intake (finding 6) ---
# The stage must be a newly created/empty owner-private non-symlink directory. Candidate
# files are created O_CREAT|O_EXCL|O_NOFOLLOW through the bound stage dirfd and fsynced;
# an existing stage entry (including a symlink redirecting outside the stage) is never
# followed or overwritten.
stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
try:
    stage_info = os.fstat(stage_fd)
    if not stat.S_ISDIR(stage_info.st_mode) or stage_info.st_uid != os.geteuid() \
       or stat.S_IMODE(stage_info.st_mode) != 0o700:
        sys.exit("prepare: stage must be an owner-private non-symlink directory (mode 700)")
    if os.listdir(stage_fd):
        sys.exit("prepare: stage must be a newly created/empty owner-private directory")
finally:
    os.close(stage_fd)

def stage_file(name, src, mode):
    # Candidate source must be an exact regular single-link bounded file (never a symlink).
    try:
        s = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        sys.exit(f"prepare: candidate source is not readable: {src}")
    try:
        sinfo = os.fstat(s)
        if not stat.S_ISREG(sinfo.st_mode) or sinfo.st_nlink != 1:
            sys.exit(f"prepare: candidate source is not a regular single-link file: {src}")
        if sinfo.st_size > MAX_CANDIDATE_BYTES:
            sys.exit(f"prepare: candidate source is oversized: {src}")
        sfd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(name, flags, mode, dir_fd=sfd)
            try:
                os.fchmod(fd, mode)
                while True:
                    chunk = os.read(s, 65536)
                    if not chunk:
                        break
                    view = memoryview(chunk)
                    written = 0
                    while written < len(view):
                        written += os.write(fd, view[written:])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(sfd)
        finally:
            os.close(sfd)
    finally:
        os.close(s)

def stage_symlink(name, target):
    sfd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            os.symlink(target, name, dir_fd=sfd)
        except FileExistsError:
            sys.exit(f"prepare: stage candidate already exists (refusing to follow/overwrite): {name}")
        os.fsync(sfd)
    finally:
        os.close(sfd)

# --- Canonical release-tree verification (finding 2) ---
# Uses the single shared helper (scripts/lib/release-tree-sha.py) - the SAME canonical
# complete-tree digest (types, paths, modes, regular bytes/hash/size, symlink target
# strings, never following links) the shell used to adopt an existing release. Recompute it
# over the installed/reused release and require it still equals the freshly built hidden
# release's digest, so a race or drift between adoption and receipt generation fails closed
# instead of self-binding a drifted release into a new prepare receipt.
def release_tree_sha(release_dir):
    out = subprocess.check_output(
        [sys.executable, os.path.join(root, "scripts", "lib", "release-tree-sha.py"), release_dir],
        text=True)
    return out.strip()


items = []
candidate_hashes = {}

def add(kind, path, had_old, src, mode=0o600, target=None):
    d = os.path.dirname(path)
    b = os.path.basename(path)
    oldp = os.path.join(d, f".pixel-restore-{b}-0-{len(items)}.old")
    newp = oldp[:-4] + ".new"
    name = os.path.basename(newp)
    entry = {"kind": kind, "path": path, "oldPath": oldp, "newPath": newp, "hadOld": had_old}
    if kind == "symlink":
        entry["target"] = target
        candidate_hashes[name] = {"type": "symlink", "target": target}
        stage_symlink(name, target)
    else:
        if src is None:
            entry["sha256"] = None
            candidate_hashes[name] = {"type": "absent"}
        else:
            if not (os.path.isfile(src) and not os.path.islink(src)):
                sys.exit(f"prepare: declared desired-present candidate source is missing: {src}")
            stage_file(name, src, mode)
            entry["sha256"] = sha256_of(os.path.join(stage, name))
            candidate_hashes[name] = {"type": "file", "sha256": entry["sha256"]}
    items.append(entry)

def exists(p):
    return 1 if os.path.exists(p) else 0

# current symlink (always present, hadOld=1) -> target release dir
add("symlink", os.path.join(install_dir, "current"), 1, None, target=f"releases/{target_pixel}")
# openclaw.json config
add("config", os.path.join(openclaw_home, "openclaw.json"), exists(os.path.join(openclaw_home, "openclaw.json")), candidate)
# gateway.env
add("config", os.path.join(agent_env_dir, "gateway.env"), exists(os.path.join(agent_env_dir, "gateway.env")),
    os.path.join(generated, "gateway.env"))
# web-courier.env: present iff the desired target is enabled; always represented (absent when
# disabled, because apply removes a pre-existing courier env).
add("config", os.path.join(agent_env_dir, "web-courier.env"),
    exists(os.path.join(agent_env_dir, "web-courier.env")),
    os.path.join(generated, "web-courier.env") if courier_enabled else None)
# gateway unit (always present) in the SEPARATE gateway unit dir.
add("unit", os.path.join(gw_unit_dir, gw_unit), exists(os.path.join(gw_unit_dir, gw_unit)),
    os.path.join(generated, "openclaw-gateway.service"))
# courier unit: present iff the desired target is enabled; always represented (absent when disabled)
# in the SEPARATE courier unit dir.
add("unit", os.path.join(courier_unit_dir, courier_unit), exists(os.path.join(courier_unit_dir, courier_unit)),
    os.path.join(generated, "pixel-web-courier.service") if courier_enabled else None)
# managed workspace files: WEB-NAVIGATION.md + browse.sh present iff web limb enabled,
# research-ledger.py always present, xfeed.sh always removed by apply (desired-absent).
if limb_web_enabled:
    add("workspace", os.path.join(workspace, "WEB-NAVIGATION.md"),
        exists(os.path.join(workspace, "WEB-NAVIGATION.md")), os.path.join(generated, "workspace", "WEB-NAVIGATION.md"))
    add("workspace", os.path.join(workspace, "scripts", "browse.sh"),
        exists(os.path.join(workspace, "scripts", "browse.sh")), os.path.join(generated, "workspace", "scripts", "browse.sh"), mode=0o700)
add("workspace", os.path.join(workspace, "scripts", "research-ledger.py"),
    exists(os.path.join(workspace, "scripts", "research-ledger.py")), os.path.join(generated, "workspace", "scripts", "research-ledger.py"), mode=0o700)
if not limb_web_enabled:
    add("workspace", os.path.join(workspace, "WEB-NAVIGATION.md"),
        exists(os.path.join(workspace, "WEB-NAVIGATION.md")), None)
    add("workspace", os.path.join(workspace, "scripts", "browse.sh"),
        exists(os.path.join(workspace, "scripts", "browse.sh")), None)
add("workspace", os.path.join(workspace, "scripts", "xfeed.sh"),
    exists(os.path.join(workspace, "scripts", "xfeed.sh")), None)

# Enforce: every desired-present candidate actually exists and matches its digest.
for it in items:
    if it["kind"] in ("config", "unit", "workspace") and it["sha256"] is not None:
        name = os.path.basename(it["newPath"])
        staged = os.path.join(stage, name)
        if not (os.path.isfile(staged) and not os.path.islink(staged)):
            sys.exit(f"prepare: desired-present candidate missing after stage: {name}")
        if sha256_of(staged) != it["sha256"]:
            sys.exit(f"prepare: staged candidate digest mismatch: {name}")

# Canonical exact-byte spec: the payload IS the file bytes (no trailing newline), so the
# writer, the Python verifier, and the privileged helper all hash the identical bytes.
spec_doc = {"deploymentItems": items, "serviceDesired": service_desired}
payload = json.dumps(spec_doc, sort_keys=True, separators=(",", ":")).encode("utf-8")

def write_new_private(path, payload_bytes, mode=0o600):
    parent = os.path.dirname(path)
    pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        pinfo = os.fstat(pfd)
        if pinfo.st_uid != os.geteuid() or (stat.S_IMODE(pinfo.st_mode) & 0o022):
            sys.exit(f"prepare: output parent is not owner-private: {parent}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(os.path.basename(path), flags, mode, dir_fd=pfd)
        try:
            os.fchmod(fd, mode)
            view = memoryview(payload_bytes)
            written = 0
            while written < len(view):
                written += os.write(fd, view[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(pfd)
    finally:
        os.close(pfd)

write_new_private(spec_out, payload)

release_dir = os.path.join(install_dir, "releases", target_pixel)
manifest = os.path.join(release_dir, "install-manifest.sha256")
identity = os.path.join(release_dir, "release-identity.json")
version_path = os.path.join(release_dir, "VERSION")
if not (os.path.isfile(manifest) and os.path.isfile(identity) and os.path.isfile(version_path)):
    sys.exit(f"prepare: installed release is incomplete under {release_dir}")
manifest_sha = sha256_of(manifest)
identity_sha = sha256_of(identity)
with open(version_path, encoding="utf-8") as f:
    release_version = f.read().strip()
tree_sha = release_tree_sha(release_dir)
# Require the installed/reused release still matches the freshly built hidden release's
# digest (the shell's adoption check used the same shared helper), so a race or drift
# between adoption and receipt generation fails closed instead of self-binding a drifted
# release into a new prepare receipt.
if tree_sha != expected_tree_sha:
    sys.exit("prepare: installed/reused release tree digest does not match the freshly built release (race/drift)")
spec_sha = hashlib.sha256(payload).hexdigest()
# bundleSha256 recomputed over EVERY exact required receipt field (never optional/defaulted).
service_desired_sha = hashlib.sha256(json.dumps(service_desired, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
bundle_payload = json.dumps(
    {"specSha256": spec_sha, "candidates": candidate_hashes,
     "installManifestSha256": manifest_sha, "releaseIdentitySha256": identity_sha,
     "releaseVersion": release_version, "releaseTreeSha256": tree_sha,
     "serviceDesiredSha256": service_desired_sha},
    sort_keys=True, separators=(",", ":")).encode("utf-8")
receipt_doc = {"mode": "migration-prepare", "targetPixel": target_pixel,
               "specSha256": spec_sha, "candidates": candidate_hashes,
               "installManifestSha256": manifest_sha,
               "releaseIdentitySha256": identity_sha,
               "releaseVersion": release_version,
               "releaseTreeSha256": tree_sha,
               "serviceDesiredSha256": service_desired_sha,
               "bundleSha256": hashlib.sha256(bundle_payload).hexdigest()}
write_new_private(receipt_out, json.dumps(receipt_doc, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
PY
[[ -s "$spec" && -s "$receipt" ]] || pixel_die "prepare writer did not produce spec/receipt"

printf '%s\n' "{\"status\":\"pass\",\"mode\":\"migration-prepare\",\"releaseDir\":\"$release\",\"spec\":\"$spec\",\"receipt\":\"$receipt\"}"
