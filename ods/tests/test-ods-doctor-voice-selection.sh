#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -f -- "$fixture/.env" "$fixture/extensions/services/whisper/compose.yaml" \
    "$fixture/extensions/services/whisper/compose.yaml.disabled" \
    "$fixture/extensions/services/tts/compose.yaml" \
    "$fixture/extensions/services/tts/compose.yaml.disabled"; \
    rmdir -- "$fixture/extensions/services/whisper" "$fixture/extensions/services/tts" \
    "$fixture/extensions/services" "$fixture/extensions" "$fixture" 2>/dev/null || true' EXIT
mkdir -p "$fixture/extensions/services/whisper" "$fixture/extensions/services/tts"
ROOT_DIR="$fixture"

# Exercise the actual doctor selection helper without running host probes.
source <(sed -n '/^_ods_doctor_voice_selected() {/,/^}/p' "$ROOT/scripts/ods-doctor.sh")
declare -F _ods_doctor_voice_selected >/dev/null

# A source checkout ships active fragments but is not an installed selection.
: >"$fixture/extensions/services/whisper/compose.yaml"
if _ods_doctor_voice_selected whisper false; then
    echo 'FAIL: source fragment without .env enabled Whisper diagnostics' >&2
    exit 1
fi

: >"$fixture/.env"
_ods_doctor_voice_selected whisper false
: >"$fixture/extensions/services/tts/compose.yaml.disabled"
if _ods_doctor_voice_selected tts true; then
    echo 'FAIL: disabled Kokoro marker lost to a stale aggregate flag' >&2
    exit 1
fi
rm -f -- "$fixture/extensions/services/tts/compose.yaml.disabled"
_ods_doctor_voice_selected tts true

echo 'PASS: doctor follows independent installed voice markers'
