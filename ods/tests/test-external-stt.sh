#!/usr/bin/env bash
# --external-stt-url: phase 03 checks a speech-to-text server on another
# machine, keeps it across reruns, and turns the local Whisper service off;
# phase 06 points Open WebUI and dashboard-api (ODS Talk) at the same server.
# curl is a stub, so nothing is contacted.
#
# The eval'd installer code reads the variables the cases set, and the
# single-quoted patterns are installer source text, not expansions.
# shellcheck disable=SC2016,SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

# shellcheck source=../installers/lib/external-services.sh
source "$ROOT/installers/lib/external-services.sh"

log() { :; }
ai() { printf '%s\n' "$*"; }
ai_ok() { printf '%s\n' "$*"; }
ai_bad() { printf '%s\n' "$*"; }
ai_warn() { printf 'WARN %s\n' "$*"; }

# MOCK_STT: models (answers a model list), empty (an empty list), html (not a
# model list) or unreachable. Every call is recorded.
curl() {
    printf '%s\n' "${*: -1}" >> "$fixture/curl-calls"
    case "$MOCK_STT" in
        models) printf '{"object":"list","data":[{"id":"Systran/faster-whisper-small"},{"id":"deepdml/faster-whisper-large-v3-turbo-ct2"}]}' ;;
        empty) printf '{"object":"list","data":[]}' ;;
        html) printf '<html>Not Found</html>' ;;
        unreachable) return 7 ;;
    esac
}

# --- URL helpers -------------------------------------------------------------
for url in http://192.168.1.20:8000 https://stt.lan http://localhost:8000/ http://10.0.0.5:8000/v1 'http://[::1]:8000'; do
    external_stt_validate_url "$url" || fail "valid URL refused: $url"
done
for url in ftp://stt.lan http://user:pw@stt.lan http://stt.lan/?x=1 http://stt.lan/#f http://stt.lan/api/v1 http://stt.lan/asr ""; do
    if external_stt_validate_url "$url"; then fail "invalid URL accepted: $url"; fi
done
[[ "$(external_stt_strip_url http://10.0.0.5:8000/v1/)" == http://10.0.0.5:8000 ]] || fail "trailing /v1/ was not stripped"
[[ "$(external_llm_container_url http://localhost:8000)" == http://host.docker.internal:8000 ]] \
    || fail "a loopback server is not reachable from containers"

MOCK_STT=models
[[ "$(external_stt_models http://stt.lan:8000 | paste -sd, -)" == "Systran/faster-whisper-small,deepdml/faster-whisper-large-v3-turbo-ct2" ]] \
    || fail "model ids were not listed"
grep -qx 'http://stt.lan:8000/v1/models' "$fixture/curl-calls" || fail "the model list was not read from /v1/models"
MOCK_STT=empty
[[ -z "$(external_stt_models http://stt.lan:8000)" ]] || fail "an empty list printed models"
for MOCK_STT in html unreachable; do
    if external_stt_models http://stt.lan:8000 >/dev/null; then fail "$MOCK_STT answer counted as a model list"; fi
done

# --- Phase 03 resolution -----------------------------------------------------
resolve_src="$(awk '
    /^_phase03_resolve_external_stt\(\) \{$/ { emit = 1 }
    emit { print }
    emit && /^}$/ { exit }
' "$ROOT/installers/phases/03-features.sh")"
[[ -n "$resolve_src" ]] || fail "could not extract _phase03_resolve_external_stt"
eval "$resolve_src"
grep -q '_phase03_resolve_external_stt || return 1' "$ROOT/installers/phases/03-features.sh" \
    || fail "phase 03 does not resolve the server before selecting Whisper"
grep -q '_sync_extension_compose "$ENABLE_LOCAL_STT"      whisper' "$ROOT/installers/phases/03-features.sh" \
    || fail "phase 03 does not keep the local Whisper off for an external server"

INSTALL_DIR="$fixture/install"
mkdir -p "$INSTALL_DIR"

# usage: resolve MOCK URL MODEL DISABLE; prints the outcome.
resolve() {
    (
        MOCK_STT="$1" EXTERNAL_STT_URL="$2" EXTERNAL_STT_MODEL="$3" EXTERNAL_STT_DISABLE="$4"
        _phase03_resolve_external_stt || { echo "rc=1"; exit 0; }
        printf 'url=%s\ncontainer=%s\nmodels=%s\n' "$EXTERNAL_STT_URL" "$EXTERNAL_STT_CONTAINER_URL" \
            "$(paste -sd, - <<<"$EXTERNAL_STT_MODELS")"
    ) 2>&1
}

: > "$INSTALL_DIR/.env"
out="$(resolve models http://localhost:8000/v1 "" false)"
grep -qx 'url=http://localhost:8000' <<<"$out" || fail "the flag URL was not selected: $out"
grep -qx 'container=http://host.docker.internal:8000' <<<"$out" || fail "containers got the loopback URL: $out"
grep -qx 'models=Systran/faster-whisper-small,deepdml/faster-whisper-large-v3-turbo-ct2' <<<"$out" \
    || fail "the listed models were not kept for phase 06: $out"

out="$(resolve models http://stt.lan:8000 Systran/faster-whisper-small false)"
grep -qx 'url=http://stt.lan:8000' <<<"$out" || fail "a served --external-stt-model was refused: $out"
out="$(resolve models http://stt.lan:8000 Systran/missing false)"
grep -qx 'rc=1' <<<"$out" || fail "a model the server does not serve was accepted: $out"
grep -qF 'Models it lists include: Systran/faster-whisper-small, deepdml/faster-whisper-large-v3-turbo-ct2' <<<"$out" \
    || fail "the refusal did not list the served models: $out"

for mock in unreachable html; do
    out="$(resolve "$mock" http://stt.lan:8000 "" false)"
    grep -qx 'rc=1' <<<"$out" || fail "a $mock server was accepted: $out"
    grep -qF -- '--no-external-stt' <<<"$out" || fail "the $mock refusal did not offer the way back: $out"
done
out="$(resolve models http://user:pw@stt.lan:8000 "" false)"
grep -qx 'rc=1' <<<"$out" || fail "a URL with credentials was accepted: $out"

# Reruns keep the saved server; --no-external-stt drops it.
printf 'EXTERNAL_STT_URL=http://stt.lan:8000\n' > "$INSTALL_DIR/.env"
out="$(resolve models "" "" false)"
grep -qx 'url=http://stt.lan:8000' <<<"$out" || fail "a rerun dropped the saved server: $out"
out="$(resolve models "" "" true)"
grep -qx 'url=' <<<"$out" || fail "--no-external-stt kept the saved server: $out"
out="$(resolve models "" Systran/faster-whisper-small true)"
grep -qx 'rc=1' <<<"$out" || fail "--external-stt-model was accepted with --no-external-stt: $out"
: > "$INSTALL_DIR/.env"
out="$(resolve models "" Systran/faster-whisper-small false)"
grep -qx 'rc=1' <<<"$out" || fail "--external-stt-model was accepted without a server: $out"

# --- Phase 06 .env values ----------------------------------------------------
P06="$ROOT/installers/phases/06-directories.sh"
env_src="$(awk '
    /^    # Speech-to-text on another machine, as phase 03 verified it/ { emit = 1 }
    emit && /^    EMBEDDING_MODEL_VALUE=/ { exit }
    emit { print }
' "$P06")"
[[ -n "$env_src" ]] || fail "could not extract the phase 06 speech-to-text block"
for line in \
    'EXTERNAL_STT_URL=$(dotenv_value "${EXTERNAL_STT_URL_VALUE}")' \
    'WHISPER_URL=$(dotenv_value "${EXTERNAL_STT_WHISPER_URL_VALUE}")' \
    'AUDIO_STT_OPENAI_API_BASE_URL=$(dotenv_value "${EXTERNAL_STT_OPENWEBUI_URL_VALUE}")'; do
    grep -qxF -- "$line" "$P06" || fail "the .env template does not write: $line"
done

# usage: env_values URL CONTAINER MODELS FLAG_MODEL AUDIO_STT_MODEL
env_values() {
    (
        EXTERNAL_STT_URL="$1" EXTERNAL_STT_CONTAINER_URL="$2" EXTERNAL_STT_MODELS="$3"
        EXTERNAL_STT_MODEL="$4" AUDIO_STT_MODEL="$5"
        eval "$env_src"
        printf 'url=%s\nwhisper=%s\nopenwebui=%s\nmodel=%s\n' "$EXTERNAL_STT_URL_VALUE" \
            "$EXTERNAL_STT_WHISPER_URL_VALUE" "$EXTERNAL_STT_OPENWEBUI_URL_VALUE" "$AUDIO_STT_MODEL"
    ) 2>&1
}

listed=$'Systran/faster-whisper-small\ndeepdml/faster-whisper-large-v3-turbo-ct2'
out="$(env_values "" "" "" "" Systran/faster-whisper-base)"
if ! grep -qx 'whisper=' <<<"$out" || ! grep -qx 'openwebui=' <<<"$out"; then
    fail "an install without a server did not leave the compose defaults: $out"
fi
# A flag value phase 03 never verified (no container URL) is not written.
out="$(env_values http://stt.lan:8000 "" "" "" Systran/faster-whisper-base)"
if ! grep -qx 'url=' <<<"$out" || ! grep -qx 'whisper=' <<<"$out"; then
    fail "an unverified server reached the .env: $out"
fi
out="$(env_values http://localhost:8000 http://host.docker.internal:8000 "$listed" "" deepdml/faster-whisper-large-v3-turbo-ct2)"
grep -qx 'url=http://localhost:8000' <<<"$out" || fail "the verified server was not saved for reruns: $out"
grep -qx 'whisper=http://host.docker.internal:8000' <<<"$out" || fail "ODS Talk was not pointed at the server: $out"
grep -qx 'openwebui=http://host.docker.internal:8000/v1' <<<"$out" || fail "Open WebUI was not pointed at the server: $out"
grep -q '^WARN' <<<"$out" && fail "a served model was warned about: $out"
out="$(env_values http://stt.lan:8000 http://stt.lan:8000 "$listed" Systran/faster-whisper-small Systran/faster-whisper-base)"
grep -qx 'model=Systran/faster-whisper-small' <<<"$out" || fail "--external-stt-model did not set AUDIO_STT_MODEL: $out"
out="$(env_values http://stt.lan:8000 http://stt.lan:8000 "$listed" "" Systran/faster-whisper-base)"
grep -qF 'WARN http://stt.lan:8000 does not list the speech-to-text model Systran/faster-whisper-base' <<<"$out" \
    || fail "an unserved AUDIO_STT_MODEL was not warned about: $out"
grep -q '^WARN' <<<"$(env_values http://stt.lan:8000 http://stt.lan:8000 "" "" Systran/faster-whisper-base)" \
    && fail "an empty model list was warned about"

# The CPU-host downgrade of large models applies to the local Whisper only.
grep -qF '[[ -z "${EXTERNAL_STT_CONTAINER_URL:-}" && "$AUDIO_STT_MODEL" =~ ([Ll]arge-v3|[Tt]urbo) ]]' "$P06" \
    || fail "a CPU host still downgrades the external server's model"

echo "PASS: --external-stt-url checks, keeps and wires a speech-to-text server on another machine"
