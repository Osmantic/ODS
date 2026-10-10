#!/usr/bin/env bash
# An installer rerun rewrites .env from phase 06's template. Settings that
# installed extensions own (declared in their manifest env_vars and written by
# their setup hooks) must survive it: Compose refuses the whole stack when an
# extension's required variable is missing, and the original secrets (for
# example LibreChat's database password) cannot be regenerated.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/06-directories.sh"
LIB="$ROOT_DIR/installers/lib/extension-env-carry.sh"
LIBRARY="$ROOT_DIR/extensions/library/services"
BUNDLED="$ROOT_DIR/extensions/services"

FAILED=0
pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1" >&2; FAILED=$((FAILED + 1)); }

[[ -f "$LIB" ]] || { fail "missing installers/lib/extension-env-carry.sh"; exit 1; }
# shellcheck source=../installers/lib/extension-env-carry.sh
. "$LIB"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
ext="$tmp/data/user-extensions"
mkdir -p "$ext/librechat" "$ext/flowise"
cp "$LIBRARY/librechat/manifest.yaml" "$ext/librechat/manifest.yaml"
cp "$LIBRARY/flowise/manifest.yaml" "$ext/flowise/manifest.yaml"

cat > "$tmp/previous.env" <<'ENV'
WEBUI_SECRET=old-webui
LIBRECHAT_MONGO_PASSWORD=mongo-original
CREDS_KEY=creds-original
CREDS_IV=iv-original
JWT_SECRET=jwt-original
JWT_REFRESH_SECRET=refresh-original
LIBRECHAT_MEILI_KEY=meili-original
FLOWISE_USERNAME=admin
FLOWISE_PASSWORD='pa$$ #word'
RETIRED_SETTING=must-not-return
ENV
printf 'WEBUI_SECRET=new-webui\nCREDS_KEY=kept-by-template\n' > "$tmp/new.env"

ods_carry_extension_env_keys "$tmp/previous.env" "$tmp/new.env" "$ext"

for line in LIBRECHAT_MONGO_PASSWORD=mongo-original CREDS_IV=iv-original JWT_SECRET=jwt-original \
            JWT_REFRESH_SECRET=refresh-original LIBRECHAT_MEILI_KEY=meili-original \
            FLOWISE_USERNAME=admin "FLOWISE_PASSWORD='pa\$\$ #word'"; do
    if grep -qxF "$line" "$tmp/new.env"; then
        pass "carried: ${line%%=*}"
    else
        fail "not carried exactly: ${line%%=*}"
    fi
done
[[ "$(grep -c '^CREDS_KEY=' "$tmp/new.env")" == 1 ]] && grep -qx 'CREDS_KEY=kept-by-template' "$tmp/new.env" \
    && pass "a key the template already wrote is not overridden or duplicated" \
    || fail "template value for CREDS_KEY was overridden or duplicated"
grep -q '^RETIRED_SETTING=' "$tmp/new.env" \
    && fail "a key no installed extension declares must not be carried" \
    || pass "undeclared keys are not resurrected"
grep -qx 'WEBUI_SECRET=new-webui' "$tmp/new.env" \
    && pass "ODS's own keys keep the template value" || fail "template value for WEBUI_SECRET changed"

cp "$tmp/new.env" "$tmp/once.env"
ods_carry_extension_env_keys "$tmp/previous.env" "$tmp/new.env" "$ext"
cmp -s "$tmp/once.env" "$tmp/new.env" && pass "a second run changes nothing" || fail "carry-over is not idempotent"

printf 'WEBUI_SECRET=x\n' > "$tmp/bare.env"
ods_carry_extension_env_keys "$tmp/previous.env" "$tmp/bare.env" "$tmp/no-extensions"
[[ "$(cat "$tmp/bare.env")" == "WEBUI_SECRET=x" ]] \
    && pass "no installed extensions: .env untouched" || fail "carry-over wrote without installed extensions"

# Bundled extensions declare owner settings the template never writes either.
bundled="$tmp/extensions/services"
for service in brave-search tailscale n8n remote-provider-egress remote-provider-ssh-tunnel; do
    mkdir -p "$bundled/$service"
    cp "$BUNDLED/$service/manifest.yaml" "$bundled/$service/manifest.yaml"
done
cat > "$tmp/owner.env" <<'ENV'
BRAVE_SEARCH_API_KEY=brave-owner-token
TS_HOSTNAME=owner-node
TS_EXTRA_ARGS=--advertise-tags=tag:ods
N8N_PROXY_HOPS=1
ODS_REMOTE_PROVIDER_ROUTE_PATH=/owner/route.json
FLOWISE_PASSWORD=flowise-owner
ENV
printf 'WEBUI_SECRET=new-webui\n' > "$tmp/owner-new.env"
ods_carry_extension_env_keys "$tmp/owner.env" "$tmp/owner-new.env" "$bundled" "$ext"
for line in BRAVE_SEARCH_API_KEY=brave-owner-token TS_HOSTNAME=owner-node \
            TS_EXTRA_ARGS=--advertise-tags=tag:ods N8N_PROXY_HOPS=1 \
            ODS_REMOTE_PROVIDER_ROUTE_PATH=/owner/route.json FLOWISE_PASSWORD=flowise-owner; do
    if [[ "$(grep -cxF "$line" "$tmp/owner-new.env")" == 1 ]]; then
        pass "carried once from bundled and installed extensions: ${line%%=*}"
    else
        fail "not carried exactly once: ${line%%=*}"
    fi
done
[[ "$(grep -c '^#=== ' "$tmp/owner-new.env")" == 1 ]] \
    && pass "one carry-over header across extension directories" || fail "carry-over header repeated"

# Phase 06 must snapshot the previous .env before its rewrite and carry the
# keys over after it.
line_of() { awk -v needle="$1" 'index($0, needle) { print NR; exit }' "$PHASE"; }
snapshot="$(line_of 'cp "$INSTALL_DIR/.env" "$_phase06_previous_env"')"
rewrite="$(line_of 'cat > "$INSTALL_DIR/.env" << ENV_EOF')"
carry="$(line_of 'ods_carry_extension_env_keys "$_phase06_previous_env" "$INSTALL_DIR/.env"')"
if [[ -n "$snapshot" && -n "$rewrite" && -n "$carry" && "$snapshot" -lt "$rewrite" && "$rewrite" -lt "$carry" ]]; then
    pass "phase 06 snapshots .env before the rewrite and carries extension keys after it"
else
    fail "phase 06 does not carry installed extensions' keys across its .env rewrite"
fi
dirs="$(line_of '"$SCRIPT_DIR/extensions/services" "$INSTALL_DIR/data/user-extensions"')"
if [[ -n "$carry" && "$dirs" == $((carry + 1)) ]]; then
    pass "phase 06 carries both bundled and installed extensions' keys"
else
    fail "phase 06 does not pass the bundled extensions to the carry-over"
fi

mkdir -p "$tmp/scoped/sample"
cat > "$tmp/scoped/sample/manifest.yaml" <<'YAML'
service:
  unrelated:
    - key: DECOY_KEY
    - key: SHARED_KEY
  env_vars:
    # Comments and blank lines must not terminate the declared settings.

    - key: SHARED_KEY
    - key: "QUOTED_KEY"
    - key: BAD.KEY
  later_section:
    - key: LATER_KEY
YAML
cat > "$tmp/scoped-old.env" <<'ENV'
DECOY_KEY=unrelated
SHARED_KEY=declared
QUOTED_KEY=quoted
BAD.KEY=invalid
LATER_KEY=unrelated
ENV
printf 'WEBUI_SECRET=current\n' > "$tmp/scoped-new.env"
chmod 600 "$tmp/scoped-new.env"
ods_carry_extension_env_keys "$tmp/scoped-old.env" "$tmp/scoped-new.env" "$tmp/scoped"
if grep -Eq '^(DECOY_KEY|LATER_KEY|BAD.KEY)=' "$tmp/scoped-new.env"; then
    fail "unrelated sections or invalid identifiers were carried"
else
    pass "only env_vars declarations with valid identifiers are carried"
fi
if grep -qx 'SHARED_KEY=declared' "$tmp/scoped-new.env" && grep -qx 'QUOTED_KEY=quoted' "$tmp/scoped-new.env"; then
    pass "declared and quoted keys survive comments and blank lines"
else
    fail "declared or quoted key missing"
fi
if [[ "$(stat -c %a "$tmp/scoped-new.env" 2>/dev/null || stat -f %Lp "$tmp/scoped-new.env")" == 600 ]]; then
    pass "carrying extension settings preserves private env permissions"
else
    fail "carrying extension settings changed private env permissions"
fi

# Owner-set public URLs (docs/ODS-PROXY.md) are never written by the
# template either; a rerun must keep them.
cat > "$tmp/urls-old.env" <<'ENV'
ODS_PUBLIC_URL=https://ods.example.com
ODS_SERVICE_PUBLIC_URLS={"open-webui":"https://chat.example.com","n8n":"https://n8n.example.com"}
OPEN_WEBUI_PUBLIC_URL=https://first.example.com
OPEN_WEBUI_PUBLIC_URL=https://chat.example.com
N8N_PUBLIC_URL=https://from-template.example.com
NOT_A_PUBLIC_URL_KEY=x
ODS_PUBLIC_URL_EXTRA=x
ENV
printf 'WEBUI_SECRET=current\nN8N_PUBLIC_URL=https://kept-template.example.com\n' > "$tmp/urls-new.env"
ods_carry_public_url_env_keys "$tmp/urls-old.env" "$tmp/urls-new.env"
for line in ODS_PUBLIC_URL=https://ods.example.com \
            'ODS_SERVICE_PUBLIC_URLS={"open-webui":"https://chat.example.com","n8n":"https://n8n.example.com"}' \
            OPEN_WEBUI_PUBLIC_URL=https://chat.example.com; do
    if [[ "$(grep -cxF "$line" "$tmp/urls-new.env")" == 1 ]]; then
        pass "public URL carried: ${line%%=*}"
    else
        fail "public URL not carried exactly once: ${line%%=*}"
    fi
done
[[ "$(grep -c '^OPEN_WEBUI_PUBLIC_URL=' "$tmp/urls-new.env")" == 1 ]] \
    && pass "the last assignment of a repeated public URL wins" || fail "a repeated public URL was carried twice"
grep -qx 'N8N_PUBLIC_URL=https://kept-template.example.com' "$tmp/urls-new.env" \
    && [[ "$(grep -c '^N8N_PUBLIC_URL=' "$tmp/urls-new.env")" == 1 ]] \
    && pass "a public URL the template wrote is not overridden" || fail "template public URL was overridden or duplicated"
grep -Eq '^(NOT_A_PUBLIC_URL_KEY|ODS_PUBLIC_URL_EXTRA)=' "$tmp/urls-new.env" \
    && fail "keys that are not public URLs were carried" || pass "only *_PUBLIC_URL and *_PUBLIC_URLS keys are carried"
cp "$tmp/urls-new.env" "$tmp/urls-once.env"
ods_carry_public_url_env_keys "$tmp/urls-old.env" "$tmp/urls-new.env"
cmp -s "$tmp/urls-once.env" "$tmp/urls-new.env" && pass "public URL carry-over is idempotent" || fail "public URL carry-over is not idempotent"
urls="$(line_of 'ods_carry_public_url_env_keys "$_phase06_previous_env" "$INSTALL_DIR/.env"')"
if [[ -n "$urls" && -n "$rewrite" && "$rewrite" -lt "$urls" ]]; then
    pass "phase 06 carries public URLs after its rewrite"
else
    fail "phase 06 does not carry public URLs across its .env rewrite"
fi

# Settings ODS tells the owner to set in .env, which no installer writes.
cat > "$tmp/owner-old.env" <<'ENV'
N8N_API_KEY=n8n-api-first
N8N_API_KEY=n8n-api-current
HF_TOKEN='hf_owner token'
LLAMA_ARC_IMAGE=ghcr.io/example/arc@sha256:abc
AUDIO_TTS_VOICE=af_bella
HF_TOKEN_EXTRA=not-listed
LLAMA_ARG_N_CPU_MOE=99
ENV
printf 'WEBUI_SECRET=current\nAUDIO_TTS_VOICE=from-template\n' > "$tmp/owner-new.env"
ods_carry_named_env_keys "$tmp/owner-old.env" "$tmp/owner-new.env" "${ODS_OWNER_ENV_KEYS[@]}"
for line in N8N_API_KEY=n8n-api-current "HF_TOKEN='hf_owner token'" LLAMA_ARC_IMAGE=ghcr.io/example/arc@sha256:abc; do
    if [[ "$(grep -cxF "$line" "$tmp/owner-new.env")" == 1 ]]; then
        pass "owner setting carried: ${line%%=*}"
    else
        fail "owner setting not carried exactly once: ${line%%=*}"
    fi
done
[[ "$(grep -c '^N8N_API_KEY=' "$tmp/owner-new.env")" == 1 ]] \
    && pass "the last assignment of an owner setting wins" || fail "an owner setting was carried twice"
grep -qx 'AUDIO_TTS_VOICE=from-template' "$tmp/owner-new.env" && [[ "$(grep -c '^AUDIO_TTS_VOICE=' "$tmp/owner-new.env")" == 1 ]] \
    && pass "an owner setting the template wrote is not overridden" || fail "template owner setting was overridden or duplicated"
grep -Eq '^(HF_TOKEN_EXTRA|LLAMA_ARG_N_CPU_MOE)=' "$tmp/owner-new.env" \
    && fail "unlisted or installer-managed keys were carried" || pass "only the listed owner settings are carried"
# A listed key the installer starts writing would let an old value override
# the installer's choice; it must then leave the list.
managed=""
for key in "${ODS_OWNER_ENV_KEYS[@]}"; do
    grep -qw "$key" "$ROOT_DIR/installers/phases/06-directories.sh" "$ROOT_DIR/installers/lib/tier-map.sh" \
        "$ROOT_DIR/install-core.sh" && managed="$managed $key"
done
[[ -z "$managed" ]] && pass "no listed owner setting is installer-managed" \
    || fail "listed owner settings are written by the installer:$managed"
owner="$(line_of 'ods_carry_named_env_keys "$_phase06_previous_env" "$INSTALL_DIR/.env" "${ODS_OWNER_ENV_KEYS[@]}"')"
if [[ -n "$owner" && -n "$rewrite" && "$rewrite" -lt "$owner" ]]; then
    pass "phase 06 carries owner settings after its rewrite"
else
    fail "phase 06 does not carry owner settings across its .env rewrite"
fi

[[ $FAILED -eq 0 ]] || { echo "$FAILED check(s) failed" >&2; exit 1; }
echo "All extension .env carry-over checks passed"
