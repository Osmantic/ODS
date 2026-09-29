#!/usr/bin/env bash
# The external model's running slot, rather than the local catalog pick,
# determines the context advertised to Pixel during installation.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/lib/external-services.sh"

ods_progress() { :; }
log() { :; }
ai() { :; }
ai_ok() { :; }
ai_bad() { printf '%s\n' "$*" >&2; }
ai_warn() { :; }
resolve_compose_config() { :; }
curl() {
    case "${*: -1}" in
        */api/tags) printf '{"models":[{"name":"qa-small"}]}' ;;
        */api/ps) printf '{"models":[{"name":"qa-small","context_length":8192}]}' ;;
        */api/v1/models)
            if [[ "${MOCK_CONTEXT_CASE:-valid}" == lmstudio ]]; then
                printf '{"models":[{"type":"llm","key":"publisher/base-model","loaded_instances":[{"id":"qa-small","config":{"context_length":8192}}],"max_context_length":131072}]}'
            else
                return 22
            fi
            ;;
        */v1/models) printf '{"data":[{"id":"qa-small"}]}' ;;
        */props)
            case "${MOCK_CONTEXT_CASE:-valid}" in
                valid) printf '{"default_generation_settings":{"n_ctx":8192},"total_slots":2,"model_alias":"qa-small"}' ;;
                high) printf '{"default_generation_settings":{"n_ctx":65536},"total_slots":1,"model_alias":"qa-small"}' ;;
                wrong-model) printf '{"default_generation_settings":{"n_ctx":8192},"model_alias":"other-model"}' ;;
                malformed) printf '{"default_generation_settings":{"n_ctx":"8192"},"model_alias":"qa-small"}' ;;
                lmstudio) return 22 ;;
                unavailable) return 22 ;;
            esac
            ;;
        *) return 22 ;;
    esac
}

INSTALL_DIR="$(mktemp -d)"
trap 'rm -rf "$INSTALL_DIR"' EXIT
INTERACTIVE=false
DRY_RUN=false
ODS_MODE=local
GGUF_FILE=local-recommendation.gguf
LLM_MODEL=local-recommendation
MAX_CONTEXT=65536
EXTERNAL_LLM_URL=http://127.0.0.1:18443
EXTERNAL_LLM_PROVIDER=openai-compatible
EXTERNAL_LLM_MODEL=qa-small

source "$ROOT_DIR/installers/phases/02b-external-services.sh"
if [[ "$MAX_CONTEXT" != 8192 ]]; then
    printf 'FAIL: Pixel would advertise %s tokens; external llama-server serves 8192 per slot\n' "$MAX_CONTEXT" >&2
    exit 1
fi
printf 'PASS: external llama-server 8192-token slot becomes installer context\n'

lmstudio="$(MOCK_CONTEXT_CASE=lmstudio; MAX_CONTEXT=65536;
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s' "$MAX_CONTEXT")"
if [[ "$lmstudio" != 8192 ]]; then
    printf 'FAIL: LM Studio selected instance context became %s\n' "$lmstudio" >&2
    exit 1
fi
printf 'PASS: LM Studio selected loaded instance context wins over model maximum\n'

ollama="$(EXTERNAL_LLM_PROVIDER=ollama; MAX_CONTEXT=65536;
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s' "$MAX_CONTEXT")"
if [[ "$ollama" != 8192 ]]; then
    printf 'FAIL: Ollama loaded model context became %s\n' "$ollama" >&2
    exit 1
fi
printf 'PASS: Ollama loaded model context wins over local recommendation\n'

weak_local="$(MAX_CONTEXT=4096; source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s' "$MAX_CONTEXT")"
if [[ "$weak_local" != 8192 ]]; then
    printf 'FAIL: unrelated local 4096-token recommendation limited external 8192 to %s\n' "$weak_local" >&2
    exit 1
fi
printf 'PASS: external serving context replaces a lower local recommendation\n'

larger_external="$(MOCK_CONTEXT_CASE=high; MAX_CONTEXT=8192;
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s' "$MAX_CONTEXT")"
if [[ "$larger_external" != 65536 ]]; then
    printf 'FAIL: unrelated local 8K recommendation limited external 64K to %s\n' "$larger_external" >&2
    exit 1
fi
printf 'PASS: loaded external 64K context remains available for Talk\n'

lower_explicit="$(EXTERNAL_LLM_CONTEXT=4096; MAX_CONTEXT=65536;
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s' "$MAX_CONTEXT")"
if [[ "$lower_explicit" != 4096 ]]; then
    printf 'FAIL: explicit external 4096-token limit was raised to %s\n' "$lower_explicit" >&2
    exit 1
fi
printf 'PASS: explicit lower external context caps observed capacity\n'

higher_explicit="$(EXTERNAL_LLM_CONTEXT=65536; MAX_CONTEXT=65536;
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s|%s' "$MAX_CONTEXT" "$EXTERNAL_LLM_CONTEXT")"
if [[ "$higher_explicit" != '8192|8192' ]]; then
    printf 'FAIL: declared 64K exceeded observed 8K at %s\n' "$higher_explicit" >&2
    exit 1
fi
printf 'PASS: observed context caps and replaces a disproved operator declaration\n'

for bad_case in wrong-model malformed unavailable; do
    if (MOCK_CONTEXT_CASE="$bad_case"; source "$ROOT_DIR/installers/phases/02b-external-services.sh") >/dev/null 2>&1; then
        printf 'FAIL: %s metadata should not silently select local 64K context\n' "$bad_case" >&2
        exit 1
    fi
    printf 'PASS: %s metadata fails closed without a declared limit\n' "$bad_case"
done

declared="$(MOCK_CONTEXT_CASE=unavailable; EXTERNAL_LLM_CONTEXT=4096;
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"; printf '%s' "$MAX_CONTEXT")"
if [[ "$declared" != 4096 ]]; then
    printf 'FAIL: operator-declared 4096-token limit became %s\n' "$declared" >&2
    exit 1
fi
printf 'PASS: unavailable metadata uses explicit operator serving limit\n'

for invalid_limit in invalid 08192 2048; do
    if (EXTERNAL_LLM_CONTEXT="$invalid_limit"; source "$ROOT_DIR/installers/phases/02b-external-services.sh") >/dev/null 2>&1; then
        printf 'FAIL: invalid external context %s was accepted\n' "$invalid_limit" >&2
        exit 1
    fi
done
printf 'PASS: invalid declared contexts fail before configuration\n'

cat > "$INSTALL_DIR/.env" <<'EOF'
EXTERNAL_LLM_URL=http://127.0.0.1:18443
EXTERNAL_LLM_PROVIDER=openai-compatible
EXTERNAL_LLM_MODEL=qa-small
EXTERNAL_LLM_CONTEXT=4096
EOF
persisted="$(MOCK_CONTEXT_CASE=unavailable; unset EXTERNAL_LLM_URL EXTERNAL_LLM_PROVIDER EXTERNAL_LLM_MODEL EXTERNAL_LLM_CONTEXT;
    MAX_CONTEXT=65536; source "$ROOT_DIR/installers/phases/02b-external-services.sh";
    printf '%s' "$MAX_CONTEXT")"
if [[ "$persisted" != 4096 ]]; then
    printf 'FAIL: reinstall lost declared external context and selected %s\n' "$persisted" >&2
    exit 1
fi
printf 'PASS: reinstall retains an operator-declared context when metadata is unavailable\n'
rm "$INSTALL_DIR/.env"

ENABLE_HERMES=true
ENABLE_PIXEL=false
ENABLE_COMFYUI=false
ENABLE_OPENCLAW=false
ENABLE_APE=false
ENABLE_PERPLEXICA=false
ENABLE_PRIVACY_SHIELD=false
ENABLE_LANGFUSE=false
INSTALL_CHOICE=1
TIER=1
DRY_RUN=true
GPU_COUNT=0
GPU_BACKEND=cpu
HOST_ARCH=x86_64
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE="$INSTALL_DIR/installer.log"
source "$ROOT_DIR/installers/phases/03-features.sh" >/dev/null
if [[ "$MAX_CONTEXT" != 8192 || "$HERMES_CONTEXT_BELOW_FLOOR" != true ]]; then
    printf 'FAIL: Hermes feature selection raised an external 8K slot to %s\n' "$MAX_CONTEXT" >&2
    exit 1
fi
printf 'PASS: Hermes does not raise external context; Talk reports below floor\n'

source "$ROOT_DIR/installers/lib/pixel-host-install.sh"
output_tokens="$(_ods_pixel_default_output_tokens "$MAX_CONTEXT")"
if [[ "$output_tokens" != 2048 ]]; then
    printf 'FAIL: Pixel output budget for 8K context is %s, expected 2048\n' "$output_tokens" >&2
    exit 1
fi
if ! python3 -c 'import os, sys; sys.exit(0 if hasattr(os, "getuid") else 1)'; then
    printf 'SKIP: native Windows Python cannot render POSIX owner-checked onboarding\n'
    exit 0
fi
printf 'qa-only-gateway-key' > "$INSTALL_DIR/gateway-key"
chmod 0600 "$INSTALL_DIR/gateway-key"
python3 "$ROOT_DIR/installers/lib/pixel-onboarding.py" \
    "$INSTALL_DIR/onboarding.json" /usr/bin/openclaw "$INSTALL_DIR" qa-small \
    "$MAX_CONTEXT" "$output_tokens" false default Default 4006 18789 \
    "$INSTALL_DIR/gateway-key" 8888 /opt/ods/pixel-plugin \
    0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef searxng '' ''
python3 - "$INSTALL_DIR/onboarding.json" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value["modelContextWindow"] == 8192
assert value["modelMaxTokens"] == 2048
PY
printf 'PASS: generated Pixel onboarding uses 8192 input context and 2048 output limit\n'
