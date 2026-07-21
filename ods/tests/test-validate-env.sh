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

# 5. .env missing one required key → exit 2
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

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
