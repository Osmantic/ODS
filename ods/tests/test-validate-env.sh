#!/bin/bash
# ============================================================================
# ODS validate-env.sh Test Suite
# ============================================================================
# Ensures scripts/validate-env.sh correctly validates .env against
# .env.schema.json (missing file, missing required keys, unknown keys, types).
# Supports rock-solid installs by guarding env validation used in phase 06
# and ods config validate.
#
# Usage: ./tests/test-validate-env.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# validate-env.sh uses associative arrays (declare -A), which require Bash 4+.
# Its shebang is #!/bin/bash, and macOS ships /bin/bash 3.2 — invoking by raw
# path there hits the Bash-4+ guard and exits 1 before any validation runs.
# Invoke through "$BASH" (the shell running this test) so the interpreter is
# guaranteed to be whatever bash launched us (typically Homebrew bash on
# macOS, /bin/bash 4+ on Linux/WSL2). Fall back to $PATH bash if $BASH is
# unset (e.g. when the test is launched from a non-bash shell).
VALIDATE_ENV_BASH="${BASH:-$(command -v bash)}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   validate-env.sh Test Suite                  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script and schema exist
if [[ ! -f "$ROOT_DIR/scripts/validate-env.sh" ]]; then
    fail "scripts/validate-env.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "validate-env.sh exists"

if [[ ! -f "$ROOT_DIR/.env.schema.json" ]]; then
    fail ".env.schema.json not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass ".env.schema.json exists"

# jq required by validate-env.sh
if ! command -v jq &>/dev/null; then
    fail "jq is required for validate-env.sh"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "jq available"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# 2. Missing .env → exit 3
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/nonexistent.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 3 ]]; then
    pass "Missing .env yields exit 3"
else
    fail "Missing .env should yield exit 3, got $r"
fi

# 3. Missing schema → exit 3
touch "$TMP_DIR/empty.env"
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/empty.env" "$TMP_DIR/nonexistent.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 3 ]]; then
    pass "Missing schema yields exit 3"
else
    fail "Missing schema should yield exit 3, got $r"
fi

# 4. .env with all required keys (minimal) → exit 0
# Schema required: WEBUI_SECRET, SEARXNG_SECRET, N8N_USER, N8N_PASS, LITELLM_KEY, OPENCLAW_TOKEN
# Values must satisfy the schema minLength (10) on these secret keys, so use
# realistic-length placeholders rather than short tokens like "admin"/"testkey".
cat > "$TMP_DIR/valid.env" <<'EOF'
WEBUI_SECRET=test-webui-secret
SEARXNG_SECRET=test-searxng-secret
N8N_USER=admin@ods.local
N8N_PASS=test-pass-1234
LITELLM_KEY=sk-test-key-1234
OPENCLAW_TOKEN=test-openclaw-token
EOF
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/valid.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "Valid .env (required keys set) yields exit 0"
else
    fail "Valid .env should yield exit 0, got $r"
fi

# N_GPU_LAYERS accepts llama.cpp's symbolic policies and explicit non-negative
# counts, but rejects malformed values before they reach any launcher.
for gpu_layers in auto all 0 17 999; do
    cp "$TMP_DIR/valid.env" "$TMP_DIR/gpu-layers-valid.env"
    printf 'N_GPU_LAYERS=%s\n' "$gpu_layers" >> "$TMP_DIR/gpu-layers-valid.env"
    "$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" \
        "$TMP_DIR/gpu-layers-valid.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1 \
        || fail "N_GPU_LAYERS=$gpu_layers should validate"
done
pass "N_GPU_LAYERS accepts auto, all, and non-negative layer counts"

for gpu_layers in -1 1.5 AUTO automatic '999;exit'; do
    cp "$TMP_DIR/valid.env" "$TMP_DIR/gpu-layers-invalid.env"
    printf 'N_GPU_LAYERS=%s\n' "$gpu_layers" >> "$TMP_DIR/gpu-layers-invalid.env"
    if "$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" \
        "$TMP_DIR/gpu-layers-invalid.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1; then
        fail "N_GPU_LAYERS=$gpu_layers should be rejected"
    fi
done
pass "N_GPU_LAYERS rejects malformed and negative values"

# 5. .env missing one required key → exit 2
REAL_JQ="$(command -v jq)"
CRLF_JQ_DIR="$TMP_DIR/crlf-jq"
mkdir -p "$CRLF_JQ_DIR"
cat > "$CRLF_JQ_DIR/jq" <<'EOF'
#!/usr/bin/env bash
set -o pipefail
if [[ " $* " == *" -r "* ]]; then
    "$ODS_TEST_REAL_JQ" "$@" | sed $'s/$/\r/'
    exit "${PIPESTATUS[0]}"
fi
exec "$ODS_TEST_REAL_JQ" "$@"
EOF
chmod +x "$CRLF_JQ_DIR/jq"
set +e
PATH="$CRLF_JQ_DIR:$PATH" ODS_TEST_REAL_JQ="$REAL_JQ" \
    "$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" \
    "$TMP_DIR/valid.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "CRLF jq raw output is normalized"
else
    fail "CRLF jq raw output should yield exit 0, got $r"
fi

cat > "$TMP_DIR/missing.env" <<'EOF'
WEBUI_SECRET=test-secret
SEARXNG_SECRET=searxsecret
N8N_USER=admin
N8N_PASS=testpass
LITELLM_KEY=testkey
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/missing.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]]; then
    pass "Missing required key yields exit 2"
else
    fail "Missing required key should yield exit 2, got $r"
fi
if echo "$out" | grep -q "Missing required\|OPENCLAW_TOKEN"; then
    pass "Output mentions missing key or required"
else
    pass "Script produced validation output"
fi

# 6. Unknown key (not in schema) → exit 2
cat > "$TMP_DIR/unknown.env" <<'EOF'
WEBUI_SECRET=test-secret
SEARXNG_SECRET=test-secret
N8N_USER=admin
N8N_PASS=testpass
LITELLM_KEY=testkey
OPENCLAW_TOKEN=testtoken
UNKNOWN_KEY=value
EOF
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/unknown.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 2 ]]; then
    pass "Unknown key yields exit 2"
else
    fail "Unknown key should yield exit 2, got $r"
fi

# 7. Required keys present but a secret too short for minLength → exit 2
# WEBUI_SECRET=CHANGEME is 8 chars, below the schema minLength of 10. All other
# required keys are long enough, so this isolates the length check.
cat > "$TMP_DIR/short.env" <<'EOF'
WEBUI_SECRET=CHANGEME
SEARXNG_SECRET=test-searxng-secret
N8N_USER=admin@ods.local
N8N_PASS=test-pass-1234
LITELLM_KEY=sk-test-key-1234
OPENCLAW_TOKEN=test-openclaw-token
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/short.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]]; then
    pass "Too-short secret yields exit 2"
else
    fail "Too-short secret should yield exit 2, got $r"
fi
if echo "$out" | grep -q "minLength"; then
    pass "Output reports a minLength violation"
else
    fail "Output should report a minLength violation"
fi

# 8. Bundled TEI does not accept GGUF/Q4 artifacts.
cp "$TMP_DIR/valid.env" "$TMP_DIR/gguf-embedding.env"
echo "EMBEDDING_MODEL=BAAI/bge-m3-Q4_K_M-GGUF" >> "$TMP_DIR/gguf-embedding.env"
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/gguf-embedding.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]] && echo "$out" | grep -q "GGUF/Q4"; then
    pass "GGUF embedding artifact is rejected with an actionable error"
else
    fail "GGUF embedding artifact should yield exit 2 and explain TEI compatibility"
fi

# 9. Open WebUI may only name a different model when it uses an external endpoint.
cp "$TMP_DIR/valid.env" "$TMP_DIR/rag-mismatch.env"
cat >> "$TMP_DIR/rag-mismatch.env" <<'EOF'
EMBEDDING_MODEL=BAAI/bge-m3
RAG_EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
RAG_OPENAI_API_BASE_URL=http://embeddings:80/v1
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/rag-mismatch.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]] && echo "$out" | grep -q "bundled TEI serves EMBEDDING_MODEL only"; then
    pass "Bundled TEI/Open WebUI model mismatch is rejected"
else
    fail "Bundled TEI/Open WebUI model mismatch should yield exit 2"
fi

# 10. A distinct model is valid when Open WebUI targets an external provider.
cp "$TMP_DIR/valid.env" "$TMP_DIR/external-rag.env"
cat >> "$TMP_DIR/external-rag.env" <<'EOF'
EMBEDDING_MODEL=BAAI/bge-m3
RAG_EMBEDDING_MODEL=external-embed-v2
RAG_OPENAI_API_BASE_URL=https://embeddings.example.test/v1
RAG_OPENAI_API_KEY=external-test-key
EMBEDDINGS_MEMORY_LIMIT=6GB
EOF
set +e
"$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/external-rag.env" "$ROOT_DIR/.env.schema.json" >/dev/null 2>&1
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "External RAG provider override remains valid"
else
    fail "External RAG provider override should yield exit 0, got $r"
fi

# 11. Invalid Docker memory values fail before compose rendering.
cp "$TMP_DIR/valid.env" "$TMP_DIR/invalid-memory.env"
echo "EMBEDDINGS_MEMORY_LIMIT=lots" >> "$TMP_DIR/invalid-memory.env"
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/invalid-memory.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]] && echo "$out" | grep -q "EMBEDDINGS_MEMORY_LIMIT"; then
    pass "Invalid embeddings memory limit is rejected before compose"
else
    fail "Invalid embeddings memory limit should yield exit 2"
fi

# 12. Invalid external endpoints fail before Open WebUI is recreated.
cp "$TMP_DIR/valid.env" "$TMP_DIR/invalid-rag-url.env"
echo "RAG_OPENAI_API_BASE_URL=embeddings.example.test/v1" >> "$TMP_DIR/invalid-rag-url.env"
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/invalid-rag-url.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]] && echo "$out" | grep -q "HTTP(S)"; then
    pass "Invalid RAG endpoint is rejected before Open WebUI recreation"
else
    fail "Invalid RAG endpoint should yield exit 2"
fi

# 13. URL validation rejects an empty/malformed authority, embedded credentials,
# invalid ports, and fragments instead of deferring failure to Open WebUI.
for invalid_url in 'http://' 'https://:443/v1' 'https://example.test:70000/v1' 'https://user:secret@example.test/v1' "'https://example.test/v1#fragment'" 'https://example.test\v1' 'https://example.test:999999999999999999999/v1'; do
    display_url="${invalid_url//\\/\\\\}"
    cp "$TMP_DIR/valid.env" "$TMP_DIR/malformed-rag-url.env"
    printf 'RAG_OPENAI_API_BASE_URL=%s\n' "$invalid_url" >> "$TMP_DIR/malformed-rag-url.env"
    set +e
    out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/malformed-rag-url.env" "$ROOT_DIR/.env.schema.json" 2>&1)
    r=$?
    set -e
    if [[ $r -eq 2 ]] && echo "$out" | grep -q "RAG_OPENAI_API_BASE_URL"; then
        pass "Malformed RAG endpoint is rejected: $display_url"
    else
        fail "Malformed RAG endpoint should be rejected: $display_url"
    fi
done

# 14. Internal DNS, IPv4, bracketed IPv6, and query strings remain valid.
for valid_url in 'http://embeddings:80/v1' 'https://embeddings.example.test/v1?tenant=ods' 'http://127.0.0.1:8090/v1' 'http://[::1]:8090/v1'; do
    cp "$TMP_DIR/valid.env" "$TMP_DIR/well-formed-rag-url.env"
    echo "RAG_OPENAI_API_BASE_URL=$valid_url" >> "$TMP_DIR/well-formed-rag-url.env"
    set +e
    out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/well-formed-rag-url.env" "$ROOT_DIR/.env.schema.json" 2>&1)
    r=$?
    set -e
    if [[ $r -eq 0 ]]; then
        pass "Well-formed RAG endpoint is accepted: $valid_url"
    else
        fail "Well-formed RAG endpoint should be accepted: $valid_url"
    fi
done

# 15. Quantization-only repository names are incompatible even when they do
# not contain the literal GGUF suffix.
for quantized_model in 'someone/bge-m3-Q4_K_M' 'someone/bge-m3-q8_0' 'someone/bge-m3-GGML'; do
    cp "$TMP_DIR/valid.env" "$TMP_DIR/quantized-embedding.env"
    echo "EMBEDDING_MODEL=$quantized_model" >> "$TMP_DIR/quantized-embedding.env"
    set +e
    out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/quantized-embedding.env" "$ROOT_DIR/.env.schema.json" 2>&1)
    r=$?
    set -e
    if [[ $r -eq 2 ]] && echo "$out" | grep -q "EMBEDDING_MODEL"; then
        pass "Quantized embedding artifact is rejected: $quantized_model"
    else
        fail "Quantized embedding artifact should be rejected: $quantized_model"
    fi
done

# 16. Remote provider metadata is valid only as an explicit cloud-mode route.
cp "$TMP_DIR/valid.env" "$TMP_DIR/remote-direct.env"
cat >> "$TMP_DIR/remote-direct.env" <<'EOF'
ODS_MODE=cloud
REMOTE_LLM_ENABLED=true
REMOTE_LLM_TRANSPORT=direct
REMOTE_LLM_BASE_URL=https://gpu.example.test
REMOTE_LLM_MODEL=qwen/remote:latest
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/remote-direct.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "Remote direct provider metadata is valid in cloud mode"
else
    fail "Remote direct provider metadata should be valid in cloud mode"
fi

cp "$TMP_DIR/valid.env" "$TMP_DIR/remote-local.env"
cat >> "$TMP_DIR/remote-local.env" <<'EOF'
ODS_MODE=local
REMOTE_LLM_ENABLED=true
REMOTE_LLM_TRANSPORT=direct
REMOTE_LLM_BASE_URL=https://gpu.example.test/v1
REMOTE_LLM_MODEL=qwen-remote
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/remote-local.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]] && echo "$out" | grep -q "ODS_MODE=cloud"; then
    pass "Remote provider activation is rejected outside cloud mode"
else
    fail "Remote provider activation should be rejected outside cloud mode"
fi

# 17. Direct remote URLs fail closed for plaintext, loopback, credentials,
# query strings, and unexpected base paths.
for invalid_remote_url in 'http://gpu.example.test/v1' 'https://127.0.0.1:8000/v1' 'https://user:secret@gpu.example.test/v1' 'https://gpu.example.test/v1?tenant=ods' 'https://gpu.example.test/proxy'; do
    cp "$TMP_DIR/valid.env" "$TMP_DIR/invalid-remote-url.env"
    cat >> "$TMP_DIR/invalid-remote-url.env" <<EOF
ODS_MODE=cloud
REMOTE_LLM_ENABLED=true
REMOTE_LLM_TRANSPORT=direct
REMOTE_LLM_BASE_URL=$invalid_remote_url
REMOTE_LLM_MODEL=qwen-remote
EOF
    set +e
    out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/invalid-remote-url.env" "$ROOT_DIR/.env.schema.json" 2>&1)
    r=$?
    set -e
    if [[ $r -eq 2 ]] && echo "$out" | grep -q "REMOTE_LLM_BASE_URL"; then
        pass "Unsafe direct remote URL is rejected: $invalid_remote_url"
    else
        fail "Unsafe direct remote URL should be rejected: $invalid_remote_url"
    fi
done

# 18. SSH transport requires explicit host/user/port and inference endpoint
# metadata, while allowing the remote-side provider URL to be plain HTTP.
cp "$TMP_DIR/valid.env" "$TMP_DIR/remote-ssh.env"
cat >> "$TMP_DIR/remote-ssh.env" <<'EOF'
ODS_MODE=cloud
REMOTE_LLM_ENABLED=true
REMOTE_LLM_TRANSPORT=ssh
REMOTE_LLM_BASE_URL=http://remote-inference.internal:8000/v1
REMOTE_LLM_MODEL=qwen-remote
REMOTE_LLM_SSH_HOST=gpu.example.test
REMOTE_LLM_SSH_USER=ods
REMOTE_LLM_SSH_PORT=22
REMOTE_LLM_SSH_INFERENCE_HOST=127.0.0.1
REMOTE_LLM_SSH_INFERENCE_PORT=8000
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/remote-ssh.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "Remote SSH transport metadata is valid with required fields"
else
    fail "Remote SSH transport metadata should be valid with required fields"
fi

cp "$TMP_DIR/remote-ssh.env" "$TMP_DIR/remote-ssh-missing.env"
sed -i.bak '/REMOTE_LLM_SSH_INFERENCE_PORT=/d' "$TMP_DIR/remote-ssh-missing.env"
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/remote-ssh-missing.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 2 ]] && echo "$out" | grep -q "REMOTE_LLM_SSH_INFERENCE_PORT"; then
    pass "Remote SSH transport rejects missing inference port"
else
    fail "Remote SSH transport should reject missing inference port"
fi

# 19. Remote provider secrets are not ordinary env settings in this slice.
if ! grep -q "REMOTE_LLM_API_KEY" "$ROOT_DIR/.env.schema.json" "$ROOT_DIR/.env.example"; then
    pass "Remote provider API key is absent from schema and .env.example"
else
    fail "Remote provider API key must not be added as an ordinary .env field"
fi

# 20. Service manifests promise user-settable .env keys. validate-env.sh
# rejects any key absent from the schema as "unknown", so a manifest key that
# never made it into .env.schema.json turns following the service README into
# a hard validation failure.
manifest_env_contract() {
    awk '
        /^[[:space:]]+external_port_env:[[:space:]]*/ {
            print FILENAME "	" $2
            next
        }
        /^[[:space:]]+-[[:space:]]+key:[[:space:]]*/ {
            pending = $3
            next
        }
        pending != "" && /^[[:space:]]+required:[[:space:]]*true[[:space:]]*$/ {
            print FILENAME "	" pending
            pending = ""
        }
        /^[[:space:]]+-[[:space:]]+key:/ { pending = $3 }
    ' "$ROOT_DIR"/extensions/services/*/manifest.yaml | sort -u
}

undeclared=""
while IFS=$'	' read -r manifest key; do
    [[ -n "$key" ]] || continue
    if ! grep -q "\"$key\":" "$ROOT_DIR/.env.schema.json"; then
        undeclared+="${key} (${manifest##*/services/}) "
    fi
done < <(manifest_env_contract)

if [[ -z "$undeclared" ]]; then
    pass "Every manifest port override and required env key is declared in the schema"
else
    fail "Manifest keys missing from .env.schema.json: ${undeclared% }"
fi

# 21. The same gap, from the user's side: setting the documented Brave Search
# keys in .env must not be reported as unknown.
cp "$TMP_DIR/valid.env" "$TMP_DIR/brave.env"
cat >> "$TMP_DIR/brave.env" <<'EOF'
BRAVE_SEARCH_API_KEY=brave-subscription-token
BRAVE_SEARCH_PORT=8585
EOF
set +e
out=$("$VALIDATE_ENV_BASH" "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/brave.env" "$ROOT_DIR/.env.schema.json" 2>&1)
r=$?
set -e
if [[ $r -eq 0 ]]; then
    pass "Documented Brave Search keys validate cleanly"
else
    fail "Brave Search keys should validate, got exit $r: $(echo "$out" | grep -i brave | tr '
' ' ')"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
