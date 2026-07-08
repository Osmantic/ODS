#!/usr/bin/env bats
# ============================================================================
# BATS tests for secret-masking helpers used by `ods config show` and
# `ods preset diff`.
# ============================================================================
# Guards against re-breakage of:
#   - PR #392: `ods config show` masked secrets from .env.schema.json
#   - PR #431: `ods preset diff` reuses the same masking helpers instead
#              of its old narrower regex
#
# Targets two file-scope helpers in ods-cli:
#   _cmd_config_load_secret_schema  — loads jq-parsed secret key list
#   _cmd_config_is_secret           — returns 0 if key is a secret
#
# Covered behavior:
#   - schema-driven match for keys with `"secret": true`
#   - keyword fallback (case-insensitive): secret|password|pass|token|key|
#     salt|bearer — still fires even when schema is loaded
#   - N8N_USER / LANGFUSE_INIT_USER_EMAIL (now schema-secret) stay masked
#   - unknown non-secret keys (e.g. ENABLE_VOICE) render plaintext
#   - missing schema → keyword fallback still works
#   - malformed schema (invalid JSON) → keyword fallback still works

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TMPDIR_TEST="$BATS_TEST_TMPDIR"

    # Extract the two file-scope helpers from ods-cli without running the
    # rest of the CLI (which would require a full install layout).
    # 1) Top-level var initialisers (two lines immediately before the first fn)
    # 2) _cmd_config_load_secret_schema body
    # 3) _cmd_config_is_secret body
    local _cli="$BATS_TEST_DIRNAME/../../ods-cli"
    eval "$(grep -E '^_cmd_config_(secret_keys|schema_loaded)=' "$_cli")"
    eval "$(awk '/^_cmd_config_load_secret_schema\(\) \{/,/^\}$/' "$_cli")"
    eval "$(awk '/^_cmd_config_is_secret\(\) \{/,/^\}$/' "$_cli")"

    # Baseline INSTALL_DIR pointing at a tmpdir; each test rewrites the
    # schema to match its scenario.
    export INSTALL_DIR="$TMPDIR_TEST/install"
    mkdir -p "$INSTALL_DIR"
}

teardown() {
    rm -rf "$TMPDIR_TEST/install"
}

# Skip tests that rely on the jq-parsed schema path. Keyword-fallback tests
# run unconditionally — they don't need jq.
_need_jq() {
    command -v jq >/dev/null 2>&1 || skip "jq not available; schema-driven masking unreachable"
}

# Build a minimal .env.schema.json with the given list of secret keys.
_write_schema() {
    local schema="$INSTALL_DIR/.env.schema.json"
    {
        echo '{'
        echo '  "properties": {'
        local first=1
        for key in "$@"; do
            if (( first )); then first=0; else echo ','; fi
            printf '    "%s": { "type": "string", "secret": true }' "$key"
        done
        # One always-plaintext key so the schema is non-empty when no secrets passed.
        if (( first )); then
            printf '    "ENABLE_VOICE": { "type": "string" }'
        else
            echo ','
            printf '    "ENABLE_VOICE": { "type": "string" }'
        fi
        echo ''
        echo '  }'
        echo '}'
    } > "$schema"
}

# ── schema-driven masking (ods config show, PR #392) ──────────────────────

@test "config-masking: key marked secret:true in schema is masked" {
    _need_jq
    _write_schema "MY_CUSTOM_SECRET"
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "MY_CUSTOM_SECRET"
    assert_success
}

@test "config-masking: unknown non-secret plain key renders plaintext" {
    _need_jq
    _write_schema "MY_CUSTOM_SECRET"
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "ENABLE_VOICE"
    assert_failure
}

# ── keyword fallback (both code paths) ──────────────────────────────────────

@test "config-masking: keyword fallback masks *secret* even if not in schema" {
    _write_schema  # only plaintext in schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "SOME_SECRET_THING"
    assert_success
}

@test "config-masking: keyword fallback masks *password*" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "ADMIN_PASSWORD"
    assert_success
}

@test "config-masking: keyword fallback masks *pass* (short form)" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "DB_PASS"
    assert_success
}

@test "config-masking: keyword fallback masks *token*" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "GITHUB_TOKEN"
    assert_success
}

@test "config-masking: keyword fallback masks *key*" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "API_KEY"
    assert_success
}

@test "config-masking: keyword fallback masks *salt*" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "AUTH_SALT"
    assert_success
}

@test "config-masking: keyword fallback masks *bearer*" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "BEARER_HEADER"
    assert_success
}

@test "config-masking: keyword fallback is case-insensitive" {
    _write_schema
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "lowercase_password"
    assert_success
    run _cmd_config_is_secret "MixedCase_Token"
    assert_success
}

# ── #431 / #994 — specific keys now flipped secret:true ─────────────────────

@test "config-masking: N8N_USER stays masked (schema path)" {
    # N8N_USER was flipped secret:true upstream. The keyword fallback does
    # NOT cover *user*, so this must come from schema-driven masking.
    _need_jq
    _write_schema "N8N_USER"
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "N8N_USER"
    assert_success
}

@test "config-masking: LANGFUSE_INIT_USER_EMAIL stays masked (schema path)" {
    # Also not covered by keyword fallback; schema-driven only.
    _need_jq
    _write_schema "LANGFUSE_INIT_USER_EMAIL"
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "LANGFUSE_INIT_USER_EMAIL"
    assert_success
}

# ── schema absence / malformation — keyword fallback still works ────────────

@test "config-masking: missing schema file → keyword fallback still fires" {
    # No schema written
    rm -f "$INSTALL_DIR/.env.schema.json"
    _cmd_config_load_secret_schema
    # Schema not loaded → schema_loaded flag is 0, fallback is the only path.
    run _cmd_config_is_secret "SOMETHING_SECRET"
    assert_success
    run _cmd_config_is_secret "SOMETHING_TOKEN"
    assert_success
}

@test "config-masking: missing schema file → plain key not masked" {
    rm -f "$INSTALL_DIR/.env.schema.json"
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "ENABLE_VOICE"
    assert_failure
}

@test "config-masking: malformed schema (invalid JSON) → fallback still works" {
    echo "{ this is not valid json" > "$INSTALL_DIR/.env.schema.json"
    _cmd_config_load_secret_schema
    # jq fails silently; schema_loaded=1 but secret_keys is empty, so
    # only the keyword fallback path can fire. Fallback catches *secret*.
    run _cmd_config_is_secret "CUSTOM_SECRET"
    assert_success
    run _cmd_config_is_secret "ENABLE_VOICE"
    assert_failure
}

@test "config-masking: schema with zero secret keys → fallback still works" {
    _need_jq
    # Valid schema, but no properties have secret:true
    cat > "$INSTALL_DIR/.env.schema.json" <<'JSON'
{ "properties": { "ENABLE_VOICE": { "type": "string" } } }
JSON
    _cmd_config_load_secret_schema
    run _cmd_config_is_secret "ANY_TOKEN"
    assert_success
    run _cmd_config_is_secret "ENABLE_VOICE"
    assert_failure
}

# ── preset diff (PR #431) uses the SAME helpers ─────────────────────────────

@test "config-masking: preset diff masking path is the same helper (smoke)" {
    # `ods preset diff` calls _cmd_config_load_secret_schema + _cmd_config_is_secret
    # on every differing key. This is the same contract as `ods config show`,
    # so if the helpers work here, diff masking works. We assert that diff-
    # relevant secret keys (keyword-matchable, so this works without jq too)
    # mask correctly post-schema-load.
    _write_schema "QDRANT_API_KEY" "WEBUI_SECRET" "LANGFUSE_SALT"
    _cmd_config_load_secret_schema
    # These all match via keyword fallback alone (key/secret/salt), so they
    # verify the helper is callable and returns correct bool even if the
    # tester lacks jq.
    run _cmd_config_is_secret "QDRANT_API_KEY"
    assert_success
    run _cmd_config_is_secret "WEBUI_SECRET"
    assert_success
    run _cmd_config_is_secret "LANGFUSE_SALT"
    assert_success
}
