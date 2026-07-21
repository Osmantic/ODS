#!/usr/bin/env bash
# Fail if migrated surfaces still assemble health probes with curl -sf.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0

if ! command -v rg >/dev/null 2>&1; then
    echo "rg required for shadow audit" >&2
    exit 1
fi

# Doctor: no hand-rolled /health curl for TTS/STT
if rg -n 'curl -sf --max-time 5 "\$\{_stt_whisper_url\}/health"' "$ROOT/scripts/ods-doctor.sh" >/dev/null; then
    echo "SHADOW: doctor still uses curl -sf for whisper /health" >&2
    FAIL=1
else
    echo "OK: doctor whisper health uses registry helper"
fi
if rg -n 'curl -sf --max-time 5 "http://127.0.0.1:\$\{TTS_PORT\}/health"' "$ROOT/scripts/ods-doctor.sh" >/dev/null; then
    echo "SHADOW: doctor still uses curl -sf for TTS /health" >&2
    FAIL=1
else
    echo "OK: doctor TTS health uses registry helper"
fi

# Showcase: check_service must take service ids, not URL+path
if rg -n 'check_service "\$' "$ROOT/scripts/showcase.sh" >/dev/null; then
    echo "SHADOW: showcase still calls check_service with URL vars" >&2
    FAIL=1
else
    echo "OK: showcase check_service uses service ids"
fi
if ! rg -n 'check_service "llama-server"' "$ROOT/scripts/showcase.sh" >/dev/null; then
    echo "SHADOW: showcase missing llama-server registry probe" >&2
    FAIL=1
fi

# CLI: host-agent and voice readiness
if rg -n 'curl -sf --max-time [23] "http://\$\{' "$ROOT/ods-cli" | rg '/health' >/dev/null; then
    echo "SHADOW: ods-cli still uses curl -sf for host-agent/voice health" >&2
    rg -n 'curl -sf --max-time [23] "http://\$\{' "$ROOT/ods-cli" | rg '/health' >&2 || true
    FAIL=1
else
    echo "OK: ods-cli host-agent/voice probes migrated"
fi
if rg -n 'curl -sf --max-time 2 "\$\{url\}/health"|curl -sf --max-time 2 "\$\{tts_url\}/health"' "$ROOT/ods-cli" >/dev/null; then
    echo "SHADOW: voice repair still uses curl -sf" >&2
    FAIL=1
else
    echo "OK: voice repair uses sr_curl_health"
fi

# Positive presence
for needle in sr_http_probe_2xx sr_curl_health; do
    if ! rg -q "$needle" "$ROOT/lib/service-registry.sh"; then
        echo "missing $needle in service-registry.sh" >&2
        FAIL=1
    fi
done
if ! rg -q 'sr_curl_health whisper' "$ROOT/scripts/ods-doctor.sh"; then
    echo "doctor missing sr_curl_health whisper" >&2
    FAIL=1
fi
if ! rg -q 'sr_http_probe_2xx' "$ROOT/ods-cli"; then
    echo "ods-cli missing sr_http_probe_2xx usage" >&2
    FAIL=1
fi

if (( FAIL )); then
    echo "HEALTH_PROBE_SHADOW_AUDIT_FAIL" >&2
    exit 1
fi
echo "HEALTH_PROBE_SHADOW_AUDIT_OK"
