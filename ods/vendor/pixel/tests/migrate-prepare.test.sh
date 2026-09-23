#!/usr/bin/env bash
# Non-live migration-only prepare phase test.
#
# Verifies migrate-prepare.sh builds the EXACT 4.3 release (through the same shared
# release-build primitive as apply.sh), requires every reviewed plan artifact, rerenders the
# config against the intended live paths and requires byte equality (no unreviewed fallback
# render), stages the exact .generated gateway/courier env + unit + workspace candidates
# (never a gateway.env fallback to openclaw.json, never a missing declared-present unit), and
# writes a strict spec + prepare receipt. It also asserts NO live managed path, service
# state, enablement, or current pointer is mutated.
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
workspace="$tmp/workspace"
agent_env_dir="$tmp/agent-env"
stage="$tmp/stage"
unit_dir="$tmp/systemd"
cache="$tmp/cache"
mkdir -p "$install_dir/releases/3.2.2/plugin" "$install_dir/releases/3.2.2/plugin-ops" \
         "$install_dir/releases/3.2.2/plugin-frontier" "$openclaw_home" "$workspace" "$agent_env_dir" \
         "$stage" "$unit_dir" "$cache" \
         "$repo/dist" "$repo/.generated/workspace/scripts" "$repo/.generated/workspace/media"

# The prepare subtransaction requires owner-private non-symlink install/releases.
chmod 700 "$install_dir" "$install_dir/releases" "$stage" "$unit_dir" "$cache"
# No runtime unit-parent override may widen the production unit-path contract: the test
# mechanically patches the copied configure.mjs so the gateway/courier unit dirs resolve to
# the harness unit_dir (the reviewed-contract binding used by production), exactly as the
# helper harness patches UNIT_PARENT.
python3 - "$repo/scripts/configure.mjs" "$unit_dir" <<'PY'
import re, sys
path, unit_dir = sys.argv[1], sys.argv[2]
src = open(path, encoding='utf-8').read()
src = src.replace('PIXEL_GATEWAY_SYSTEMD_DIR: "/etc/systemd/system"', f'PIXEL_GATEWAY_SYSTEMD_DIR: "{unit_dir}"')
src = src.replace('PIXEL_COURIER_SYSTEMD_DIR: "/etc/systemd/system"', f'PIXEL_COURIER_SYSTEMD_DIR: "{unit_dir}"')
open(path, 'w', encoding='utf-8').write(src)
PY

# Live plugin trees mirror the reviewed repo plugin trees under the current release target
# (current -> releases/3.2.2), so render against current/plugin is byte-stable.
cp -r "$repo/plugin/." "$install_dir/releases/3.2.2/plugin/"
cp -r "$repo/plugin-ops/." "$install_dir/releases/3.2.2/plugin-ops/"
cp -r "$repo/plugin-frontier/." "$install_dir/releases/3.2.2/plugin-frontier/"
printf 'legacy-release' > "$install_dir/releases/3.2.2/VERSION"
ln -s "releases/3.2.2" "$install_dir/current"
printf '{"legacy":true}' > "$openclaw_home/openclaw.json"
printf 'GATEWAY_OLD=1\n' > "$agent_env_dir/gateway.env"
printf 'COURIER_OLD=1\n' > "$agent_env_dir/web-courier.env"
printf 'COURIER_UNIT_OLD=1\n' > "$unit_dir/pixel-web-courier.service"
printf '[Unit]\nDescription=legacy gateway\n' > "$unit_dir/openclaw-gateway.service"

cat > "$repo/.env" <<ENV
PIXEL_INSTALL_DIR=$install_dir
PIXEL_WORKSPACE=$workspace
OPENCLAW_HOME=$openclaw_home
PIXEL_RELEASE_VERSION=4.3.27
PIXEL_MODEL_PROVIDER=openai
PIXEL_MODEL_ID=gpt-4o
PIXEL_MODEL_NAME=GPT-4o
PIXEL_MODEL_API_KEY=test
PIXEL_MODEL_BASE_URL=http://127.0.0.1:9999/v1
PIXEL_MODEL_REASONING=1
PIXEL_MODEL_CONTEXT_WINDOW=128000
PIXEL_MODEL_MAX_TOKENS=4096
PIXEL_GATEWAY_TOKEN=tok
PIXEL_AGENT_ID=pixel
PIXEL_AGENT_NAME=Pixel
PIXEL_SANDBOX_IMAGE=debian:bookworm-slim
PIXEL_EMBEDDING_MODEL=text-embedding
PIXEL_EMBEDDING_CACHE=$cache
PIXEL_SEARXNG_BASE_URL=http://127.0.0.1:8888
PIXEL_LIMB_EMAIL_ENABLED=0
PIXEL_LIMB_CALENDAR_ENABLED=0
PIXEL_LIMB_SOCIAL_ENABLED=0
PIXEL_LIMB_WEB_ENABLED=1
PIXEL_LIMB_OPERATIONS_ENABLED=0
PIXEL_LIMB_FRONTIER_ENABLED=0
PIXEL_SOURCE_BROKER_ENABLED=0
PIXEL_OPS_BROKER_ENABLED=0
PIXEL_FRONTIER_BROKER_ENABLED=0
PIXEL_WEB_COURIER_ENABLED=0
PIXEL_SYSTEMD_UNIT=openclaw-gateway.service
PIXEL_WEB_COURIER_UNIT=pixel-web-courier.service
ENV

# Produce the reviewed plan exactly as plan.sh would: render the candidate against the live
# plugin paths, then checksum it, then build the deployment/source-runtime hashes.
(
  cd "$repo"
  export OPENCLAW_HOME PIXEL_WORKSPACE PIXEL_PLUGIN_PATH="$install_dir/current/plugin" \
         PIXEL_OPS_PLUGIN_PATH="$install_dir/current/plugin-ops" \
         PIXEL_FRONTIER_PLUGIN_PATH="$install_dir/current/plugin-frontier"
  # shellcheck disable=SC1091
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  export PIXEL_PLUGIN_PATH="$install_dir/current/plugin" \
         PIXEL_OPS_PLUGIN_PATH="$install_dir/current/plugin-ops" \
         PIXEL_FRONTIER_PLUGIN_PATH="$install_dir/current/plugin-frontier"
  node scripts/render-config.mjs dist/openclaw.json >/dev/null
  chmod 600 dist/openclaw.json
  ( cd dist && sha256sum openclaw.json > openclaw.sha256 )
  printf '{"identity":"release"}' > dist/release-identity.json
  printf '{"runtime":"x"}' > dist/source-runtime.sha256
  # .generated reviewed outputs (gateway/courier env + units + workspace).
  printf 'GATEWAY_4_2=1\n' > .generated/gateway.env
  printf 'COURIER_4_2=1\n' > .generated/web-courier.env
  printf '[Unit]\nDescription=gateway 4.3\n' > .generated/openclaw-gateway.service
  printf '[Unit]\nDescription=courier 4.3\n' > .generated/pixel-web-courier.service
  printf '# nav 4.3\n' > .generated/workspace/WEB-NAVIGATION.md
  printf '#!/usr/bin/env bash\n' > .generated/workspace/scripts/browse.sh
  printf '#!/usr/bin/env bash\n' > .generated/workspace/scripts/research-ledger.py
  chmod 700 .generated/workspace/scripts/browse.sh .generated/workspace/scripts/research-ledger.py
  # Also create the other .generated artifacts referenced by the deployment hash so the
  # checksum file is consistent over the repo.
  touch .generated/deployment.json .generated/source-broker.env .generated/pixel-source-broker.service \
        .generated/pixel-source-broker.timer .generated/pixel-source-action@.service \
        .generated/pixel-source-reconcile@.service .generated/ops-broker.env .generated/ops-policy.json \
        .generated/pixel-ops-broker.service .generated/frontier-broker.env .generated/frontier-policy.json \
        .generated/pixel-frontier-broker.service
  # Deployment hash over every tracked source file the plan reviews (node_modules and
  # .git excluded), so sha256sum -c from the repo root is consistent.
  find . -path ./.git -prune -o -path '*/node_modules' -prune -o \
    -path './dist/deployment.sha256' -prune -o -type f -print0 \
    | LC_ALL=C sort -zu | xargs -0 sha256sum > dist/deployment.sha256
)

before_current=$(readlink "$install_dir/current")
before_openclaw=$(cat "$openclaw_home/openclaw.json")
before_gw=$(cat "$agent_env_dir/gateway.env")
before_courier_env=$(cat "$agent_env_dir/web-courier.env")

spec="$tmp/spec.json"
receipt="$tmp/receipt.json"
cd "$repo"
bash scripts/migrate-prepare.sh \
  --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir" \
  --openclaw-home "$openclaw_home" --stage "$stage" --spec "$spec" --receipt "$receipt" \
  > "$tmp/prepare.out"
grep -F '"mode":"migration-prepare"' "$tmp/prepare.out" >/dev/null

# --- Non-live: no live managed path changed ---
[[ "$(readlink "$install_dir/current")" == "$before_current" ]]
[[ "$(cat "$openclaw_home/openclaw.json")" == "$before_openclaw" ]]
[[ "$(cat "$agent_env_dir/gateway.env")" == "$before_gw" ]]
[[ "$(cat "$agent_env_dir/web-courier.env")" == "$before_courier_env" ]]

# --- Exact 4.3 release installed atomically under releases (NOT active live state) with a
# COMPLETE manifest; current + config + workspace + units + services are unchanged. ---
[[ -d "$install_dir/releases/4.3.27" ]]
[[ ! -e "$stage/releases/4.3.27" ]]
for artifact in VERSION release-identity.json deployment-inputs.sha256 source-runtime.sha256 openclaw.sha256 install-manifest.sha256; do
  [[ -f "$install_dir/releases/4.3.27/$artifact" ]] || { echo "missing release artifact: $artifact" >&2; exit 1; }
done

# --- Release-identity loader fails closed on an unsafe/changed identity. The shell loader
# derives the exact target from $ROOT/VERSION + RELEASE-MANIFEST before argument parsing and
# must reject a symlinked (pathname swap) or hardlinked (nlink>1) identity, never accept it.
cp "$repo/VERSION" "$tmp/VERSION.pristine"
mv "$repo/VERSION" "$tmp/VERSION.real"
printf '4.3.9\n' > "$tmp/VERSION.link-target"
ln -s "$tmp/VERSION.link-target" "$repo/VERSION"
mkdir -p "$tmp/stage-id-sym"
chmod 700 "$tmp/stage-id-sym"
if (
  cd "$repo"
  bash scripts/migrate-prepare.sh --install-dir "$install_dir" --workspace "$workspace" \
    --agent-env-dir "$agent_env_dir" --openclaw-home "$openclaw_home" \
    --stage "$tmp/stage-id-sym" --spec "$tmp/spec-id-sym.json" \
    --receipt "$tmp/receipt-id-sym.json" > "$tmp/identity-sym.out" 2>&1
); then
  echo "migrate-prepare accepted a symlinked VERSION identity" >&2; exit 1
fi
grep -F "release VERSION" "$tmp/identity-sym.out" >/dev/null \
  || { echo "migrate-prepare did not reject a symlinked VERSION identity" >&2; cat "$tmp/identity-sym.out" >&2; exit 1; }
rm -f "$repo/VERSION"
mv "$tmp/VERSION.real" "$repo/VERSION"

# Hardlink identity (nlink==2) must also be rejected.
mv "$repo/VERSION" "$tmp/VERSION.real2"
ln "$tmp/VERSION.real2" "$repo/VERSION"
mkdir -p "$tmp/stage-id-hl"
chmod 700 "$tmp/stage-id-hl"
if (
  cd "$repo"
  bash scripts/migrate-prepare.sh --install-dir "$install_dir" --workspace "$workspace" \
    --agent-env-dir "$agent_env_dir" --openclaw-home "$openclaw_home" \
    --stage "$tmp/stage-id-hl" --spec "$tmp/spec-id-hl.json" \
    --receipt "$tmp/receipt-id-hl.json" > "$tmp/identity-hl.out" 2>&1
); then
  echo "migrate-prepare accepted a hardlinked VERSION identity" >&2; exit 1
fi
grep -F "release VERSION" "$tmp/identity-hl.out" >/dev/null \
  || { echo "migrate-prepare did not reject a hardlinked VERSION identity" >&2; cat "$tmp/identity-hl.out" >&2; exit 1; }
rm -f "$repo/VERSION"
mv "$tmp/VERSION.real2" "$repo/VERSION"

# --- Parity: the prepare release tree is byte-equivalent to what apply builds (shared
# primitive, same env). Build a second tree via the primitive and compare manifests. ---
(
  cd "$repo"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  source scripts/lib/release-build.sh
  pixel_build_release_stage "$repo" "$tmp/apply-release"
)
(cd "$install_dir/releases/4.3.27" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$tmp/prep-manifest"
(cd "$tmp/apply-release" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$tmp/apply-manifest"
cmp -s "$tmp/prep-manifest" "$tmp/apply-manifest" || { echo "prepare release != apply release (drift)" >&2; exit 1; }

# --- Exact env staging: gateway.env candidate equals .generated/gateway.env, never a
# fallback to openclaw.json. web-courier is disabled -> desired-absent (no candidate). ---
gw_name=$(python3 - "$spec" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for it in d["deploymentItems"]:
    if it["path"].endswith("/gateway.env"): print(__import__("os").path.basename(it["newPath"])); break
PY
)
cmp -s "$stage/$gw_name" "$repo/.generated/gateway.env" || { echo "gateway.env candidate is not the exact .generated file" >&2; exit 1; }
[[ "$(cat "$stage/$gw_name")" != "$(cat "$repo/dist/openclaw.json")" ]] || { echo "gateway.env fell back to openclaw.json" >&2; exit 1; }

# --- Unit candidates exist and equal exact .generated (no declared-present missing) ---
python3 - "$spec" "$stage" "$repo" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1])); stage = sys.argv[2]; repo = sys.argv[3]
for it in d["deploymentItems"]:
    if it["kind"] == "unit" and it.get("sha256") is not None:
        name = os.path.basename(it["newPath"])
        assert os.path.isfile(os.path.join(stage, name)), "desired-present unit candidate missing: " + name
        if it["path"].endswith("openclaw-gateway.service"):
            assert open(os.path.join(stage, name)).read() == open(os.path.join(repo, ".generated", "openclaw-gateway.service")).read()
PY

# --- Strict spec: exact item keys with per-item digest; absent items carry sha256 null ---
python3 - "$spec" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1]))
assert set(d) == {"deploymentItems", "serviceDesired"}
items = d["deploymentItems"]
assert items
sd = d["serviceDesired"]
assert isinstance(sd, dict) and sd
for unit, rec in sd.items():
    assert isinstance(unit, str) and set(rec) == {"enabled", "active"}
    assert isinstance(rec["enabled"], bool) and isinstance(rec["active"], bool)
# Requirement 2: serviceDesired keys must EXACTLY equal the fixed non-deep units in the
# affected-unit set (no extras, no dynamic deep-work units).
assert set(sd) == {"openclaw-gateway.service", "pixel-web-courier.service",
                   "pixel-source-broker.timer", "pixel-ops-broker.service",
                   "pixel-frontier-broker.service"}, sd
assert sd.get("openclaw-gateway.service") == {"enabled": True, "active": True}
assert sd.get("pixel-web-courier.service") == {"enabled": False, "active": False}
seen = set()
for it in items:
    assert set(it) <= {"kind", "path", "oldPath", "newPath", "hadOld", "sha256", "target"}, it
    assert os.path.dirname(it["oldPath"]) == os.path.dirname(it["path"])
    assert it["newPath"] == it["oldPath"][:-4] + ".new"
    assert it["path"] not in seen
    seen.add(it["path"])
    if it["kind"] == "symlink":
        assert it["target"] == "releases/4.3.27"
    elif it["kind"] in ("config", "unit", "workspace"):
        assert "sha256" in it
# xfeed.sh is desired-absent -> sha256 None.
xfeed = [it for it in items if it["path"].endswith("/scripts/xfeed.sh")]
assert xfeed and xfeed[0]["sha256"] is None
# web-courier disabled -> courier env + courier unit are desired-absent (sha256 None).
courier_env_item = [it for it in items if it["path"].endswith("/web-courier.env")]
courier_unit_item = [it for it in items if it["path"].endswith("/pixel-web-courier.service")]
assert courier_env_item and courier_env_item[0]["sha256"] is None
assert courier_unit_item and courier_unit_item[0]["sha256"] is None
PY

# --- Prepare receipt binds the complete bundle + installed release identity ---
python3 - "$receipt" "$spec" <<'PY'
import hashlib, json, os, sys
rec = json.load(open(sys.argv[1])); spec = json.load(open(sys.argv[2]))
assert rec["mode"] == "migration-prepare" and rec["targetPixel"] == "4.3.27"
assert "bundleSha256" in rec and "installManifestSha256" in rec and "candidates" in rec
assert rec["specSha256"] == hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
# The exact installed release identity + version are bound to the private prepare receipt.
assert rec["releaseVersion"] == "4.3.27"
assert len(rec["releaseIdentitySha256"]) == 64
PY

# --- Idempotent exact-existing result: re-run prepare succeeds and is a safe no-op. ---
mkdir -p "$tmp/stage2"
chmod 700 "$tmp/stage2"
(
  cd "$repo"
  bash scripts/migrate-prepare.sh \
    --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir" \
    --openclaw-home "$openclaw_home" --stage "$tmp/stage2" --spec "$tmp/spec2.json" --receipt "$tmp/receipt2.json" \
    > "$tmp/prepare2.out"
)
grep -F '"status":"pass"' "$tmp/prepare2.out" >/dev/null
cmp -s "$tmp/receipt2.json" "$receipt" || { echo "idempotent re-run changed the receipt" >&2; exit 1; }

# --- Item 4: existing-release adoption requires an EXACT canonical complete-tree digest,
# not just a matching manifest. Perturbations a per-file manifest check misses (extra
# directory, extra symlink, changed file mode) must be rejected; the exact existing
# release retry above is the accepted case. Each perturbation is undone and the exact tree
# re-confirmed before the next. ---
reject_existing_release() {
  # shellcheck disable=SC2016
  local stage_dir=$1 out_file=$2
  mkdir -p "$stage_dir"
  chmod 700 "$stage_dir"
  if (
    cd "$repo"
    bash scripts/migrate-prepare.sh     --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir"     --openclaw-home "$openclaw_home" --stage "$stage_dir" --spec "$tmp/spec-x.json" --receipt "$tmp/receipt-x.json"
  ) > "$out_file" 2>&1; then
    echo "non-exact existing 4.3 release (item 4) was NOT rejected" >&2; exit 1
  fi
  grep -F "non-exact 4.3.27 release already exists" "$out_file" >/dev/null
}
# (a) extra directory: an empty dir is invisible to find -type f, so the manifest check
# alone would silently adopt it; the complete-tree digest must reject it.
mkdir -p "$install_dir/releases/4.3.27/extra-dir"
reject_existing_release "$tmp/stage-extra-dir" "$tmp/prepare-extra-dir.out"
rmdir "$install_dir/releases/4.3.27/extra-dir"
# (b) extra symlink: a symlink is not a regular file, so the manifest check alone would
# silently adopt it; the complete-tree digest must reject it.
ln -s /usr/bin/true "$install_dir/releases/4.3.27/extra-link"
reject_existing_release "$tmp/stage-extra-link" "$tmp/prepare-extra-link.out"
rm -f "$install_dir/releases/4.3.27/extra-link"
# (c) file mode change: content/manifest are unchanged, so only the complete-tree digest
# (which binds modes) can reject it. Capture the exact pristine mode first so the restore
# matches what the shared build primitive produces (cp preserves the repo VERSION mode).
pristine_version_mode=$(stat -c '%a' "$install_dir/releases/4.3.27/VERSION")
chmod 0640 "$install_dir/releases/4.3.27/VERSION"
reject_existing_release "$tmp/stage-mode" "$tmp/prepare-mode.out"
chmod "$pristine_version_mode" "$install_dir/releases/4.3.27/VERSION"
# Re-confirm the exact restored tree is still an accepted idempotent retry.
mkdir -p "$tmp/stage-restore"
chmod 700 "$tmp/stage-restore"
(
  cd "$repo"
  bash scripts/migrate-prepare.sh     --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir"     --openclaw-home "$openclaw_home" --stage "$tmp/stage-restore" --spec "$tmp/spec-restore.json" --receipt "$tmp/receipt-restore.json"     > "$tmp/prepare-restore.out"
)
grep -F '"status":"pass"' "$tmp/prepare-restore.out" >/dev/null

# (d) changed symlink target (and legitimate absolute/.. targets) at the helper level: the
# SAME shared pixel_release_tree_sha the adoption check uses. A changed symlink target must
# change the digest (so adoption rejects it) while legitimate absolute and relative-with-..
# targets are accepted by text (never followed).
mkdir -p "$tmp/symA/bin" "$tmp/symB/bin" "$tmp/symC/bin"
printf 'release' > "$tmp/symA/VERSION"
( cd "$tmp/symA" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$tmp/symA-manifest.tmp" )
mv "$tmp/symA-manifest.tmp" "$tmp/symA/install-manifest.sha256"
cp -r "$tmp/symA/." "$tmp/symB/"
cp -r "$tmp/symA/." "$tmp/symC/"
ln -s /usr/bin/python3 "$tmp/symA/bin/python"
ln -s /usr/bin/python2 "$tmp/symB/bin/python"
ln -s ../lib/python3.11 "$tmp/symC/bin/python"
# shellcheck source=scripts/lib/release-build.sh
source "$repo/scripts/lib/release-build.sh"
sha_exact=$(pixel_release_tree_sha "$tmp/symA")
sha_exact2=$(pixel_release_tree_sha "$tmp/symA")
sha_changed_target=$(pixel_release_tree_sha "$tmp/symB")
sha_dotdot_target=$(pixel_release_tree_sha "$tmp/symC")
[[ "$sha_exact" == "$sha_exact2" ]] || { echo "exact tree digest is not stable" >&2; exit 1; }
[[ "$sha_changed_target" != "$sha_exact" ]] || { echo "changed symlink target did not change the digest" >&2; exit 1; }
[[ "$sha_dotdot_target" != "$sha_exact" ]] || { echo "relative-.. symlink target did not bind its distinct string" >&2; exit 1; }

# --- Requirement 7: a loaded actual unit-dir env value that drifts from the authored
# configured dir is rejected BEFORE any release build (gateway and courier separately). ---
mismatch_dir="$tmp/mismatch-systemd"
mkdir -p "$mismatch_dir" "$tmp/stage-gw-mismatch" "$tmp/stage-co-mismatch"
chmod 700 "$mismatch_dir" "$tmp/stage-gw-mismatch" "$tmp/stage-co-mismatch"
# Gateway mismatch.
cp "$repo/.env" "$tmp/env.pristine"
printf 'PIXEL_GATEWAY_SYSTEMD_DIR=%s\n' "$mismatch_dir" >> "$repo/.env"
if (
  cd "$repo"
  bash scripts/migrate-prepare.sh     --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir"     --openclaw-home "$openclaw_home" --stage "$tmp/stage-gw-mismatch" --spec "$tmp/spec-gw-mismatch.json" --receipt "$tmp/receipt-gw-mismatch.json"
) > "$tmp/prepare-gw-mismatch.out" 2>&1; then
  echo "gateway unit-dir env mismatch was NOT rejected" >&2; exit 1
fi
grep -F "gateway unit dir mismatch" "$tmp/prepare-gw-mismatch.out" >/dev/null
cp "$tmp/env.pristine" "$repo/.env"
# Courier mismatch.
printf 'PIXEL_COURIER_SYSTEMD_DIR=%s\n' "$mismatch_dir" >> "$repo/.env"
if (
  cd "$repo"
  bash scripts/migrate-prepare.sh     --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir"     --openclaw-home "$openclaw_home" --stage "$tmp/stage-co-mismatch" --spec "$tmp/spec-co-mismatch.json" --receipt "$tmp/receipt-co-mismatch.json"
) > "$tmp/prepare-co-mismatch.out" 2>&1; then
  echo "courier unit-dir env mismatch was NOT rejected" >&2; exit 1
fi
grep -F "courier unit dir mismatch" "$tmp/prepare-co-mismatch.out" >/dev/null
cp "$tmp/env.pristine" "$repo/.env"

# --- Reject a non-exact pre-existing 4.3 release (tampered install-manifest). ---
mkdir -p "$tmp/stage3"
chmod 700 "$tmp/stage3"
cp "$install_dir/releases/4.3.27/install-manifest.sha256" "$tmp/pristine-manifest"
printf 'tampered' >> "$install_dir/releases/4.3.27/install-manifest.sha256"
if (
  cd "$repo"
  bash scripts/migrate-prepare.sh \
    --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir" \
    --openclaw-home "$openclaw_home" --stage "$tmp/stage3" --spec "$tmp/spec3.json" --receipt "$tmp/receipt3.json"
) > "$tmp/prepare3.out" 2>&1; then
  echo "non-exact pre-existing 4.3 release was NOT rejected" >&2; exit 1
fi
grep -F "non-exact 4.3.27 release already exists" "$tmp/prepare3.out" >/dev/null
# Restore the exact manifest for the subsequent exact prepare.
cp "$tmp/pristine-manifest" "$install_dir/releases/4.3.27/install-manifest.sha256"

# --- Stale hidden siblings are NEVER auto-deleted: filename + owner is not exact evidence.
# An unknown same-pattern hidden stage directory and its bytes are left inert and preserved
# while a new unique prepare still succeeds. The process trap removes only the exact hidden
# path it created. ---
mkdir -p "$tmp/stage4"
chmod 700 "$tmp/stage4"
stale="$install_dir/releases/.4.3.27.prepare.999999"
mkdir -p "$stale/plugin"
printf 'do-not-delete' > "$stale/SENTINEL"
cp "$install_dir/releases/4.3.27/VERSION" "$stale/VERSION"
cp "$install_dir/releases/4.3.27/install-manifest.sha256" "$stale/install-manifest.sha256"
cp "$install_dir/releases/4.3.27/release-identity.json" "$stale/release-identity.json"
cp "$install_dir/releases/4.3.27/deployment-inputs.sha256" "$stale/deployment-inputs.sha256"
cp "$install_dir/releases/4.3.27/source-runtime.sha256" "$stale/source-runtime.sha256"
chmod 700 "$stale"
(
  cd "$repo"
  bash scripts/migrate-prepare.sh \
    --install-dir "$install_dir" --workspace "$workspace" --agent-env-dir "$agent_env_dir" \
    --openclaw-home "$openclaw_home" --stage "$tmp/stage4" --spec "$tmp/spec4.json" --receipt "$tmp/receipt4.json" \
    > "$tmp/prepare4.out" 2>&1
)
[[ -d "$stale" ]] || { echo "unknown stale hidden stage sibling was deleted" >&2; exit 1; }
[[ "$(cat "$stale/SENTINEL")" == "do-not-delete" ]] || { echo "unknown stale hidden stage sibling bytes were changed" >&2; exit 1; }

echo "migrate-prepare non-live phase test passed"
