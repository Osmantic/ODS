#!/usr/bin/env bash
# Fail if migrated surfaces still assemble health probes with curl -sf.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$ROOT/.." && pwd)"
FAIL=0

if ! command -v rg >/dev/null 2>&1; then
    echo "rg required for shadow audit" >&2
    exit 1
fi

ok() { echo "OK: $1"; }
bad() { echo "SHADOW: $1" >&2; FAIL=1; }

# Doctor: no hand-rolled /health curl for TTS/STT / dashboard / webui
if rg -n 'curl -sf --max-time 5 "\$\{_stt_whisper_url\}/health"' "$ROOT/scripts/ods-doctor.sh" >/dev/null; then
    bad "doctor still uses curl -sf for whisper /health"
else
    ok "doctor whisper health uses registry helper"
fi
if rg -n 'curl -sf --max-time 5 "http://127.0.0.1:\$\{TTS_PORT\}/health"' "$ROOT/scripts/ods-doctor.sh" >/dev/null; then
    bad "doctor still uses curl -sf for TTS /health"
else
    ok "doctor TTS health uses registry helper"
fi
if rg -n 'curl -sf --max-time 10 "http://127.0.0.1:\$\{_DASHBOARD_PORT\}"' "$ROOT/scripts/ods-doctor.sh" >/dev/null; then
    bad "doctor still uses curl -sf for dashboard HTTP"
else
    ok "doctor dashboard uses registry/2xx helper"
fi
if rg -n 'curl -sf --max-time 10 "http://127.0.0.1:\$\{_WEBUI_PORT\}"' "$ROOT/scripts/ods-doctor.sh" >/dev/null; then
    bad "doctor still uses curl -sf for webui HTTP"
else
    ok "doctor webui uses registry/2xx helper"
fi

# Showcase: check_service must take service ids
if rg -n 'check_service "\$' "$ROOT/scripts/showcase.sh" >/dev/null; then
    bad "showcase still calls check_service with URL vars"
else
    ok "showcase check_service uses service ids"
fi
if ! rg -n 'check_service "llama-server"' "$ROOT/scripts/showcase.sh" >/dev/null; then
    bad "showcase missing llama-server registry probe"
fi

# first-boot-demo: registry ids, no URL+path health curls
if rg -n 'check_service "' "$ROOT/scripts/first-boot-demo.sh" >/dev/null; then
    bad "first-boot-demo still has legacy check_service(name,url,path)"
else
    ok "first-boot-demo uses check_service_id"
fi
if ! rg -n 'check_service_id "LLM \(llama-server\)" "llama-server"' "$ROOT/scripts/first-boot-demo.sh" >/dev/null; then
    bad "first-boot-demo missing llama-server registry probe"
fi
if rg -n 'curl -sf "\$\{WHISPER_URL\}/health"|curl -sf "\$\{N8N_URL\}/healthz"|curl -sf "\$\{PIPER_URL\}"' "$ROOT/scripts/first-boot-demo.sh" >/dev/null; then
    bad "first-boot-demo still uses curl -sf for optional service health"
else
    ok "first-boot-demo optional services use registry probes"
fi

# validate.sh core health endpoints via registry
if rg -n 'check "llama-server health" "curl -sf' "$ROOT/scripts/validate.sh" >/dev/null; then
    bad "validate.sh still hand-rolls llama-server health curl"
else
    ok "validate.sh llama-server health uses registry"
fi
if ! rg -n 'check_registry_health "llama-server health" "llama-server"' "$ROOT/scripts/validate.sh" >/dev/null; then
    bad "validate.sh missing check_registry_health llama-server"
fi
if ! rg -n 'check_registry_health "WebUI reachable" "open-webui"' "$ROOT/scripts/validate.sh" >/dev/null; then
    bad "validate.sh missing check_registry_health open-webui"
fi

# CLI: host-agent and voice readiness
if rg -n 'curl -sf --max-time [23] "http://\$\{' "$ROOT/ods-cli" | rg '/health' >/dev/null; then
    bad "ods-cli still uses curl -sf for host-agent/voice health"
    rg -n 'curl -sf --max-time [23] "http://\$\{' "$ROOT/ods-cli" | rg '/health' >&2 || true
else
    ok "ods-cli host-agent/voice probes migrated"
fi
if rg -n 'curl -sf --max-time 2 "\$\{url\}/health"|curl -sf --max-time 2 "\$\{tts_url\}/health"' "$ROOT/ods-cli" >/dev/null; then
    bad "voice repair still uses curl -sf"
else
    ok "voice repair uses sr_curl_health"
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
if ! rg -q 'sr_curl_health dashboard\|sr_curl_health open-webui' "$ROOT/scripts/ods-doctor.sh"; then
    # either dashboard or open-webui must appear with sr_curl_health
    if ! rg -q 'sr_curl_health dashboard' "$ROOT/scripts/ods-doctor.sh" || ! rg -q 'sr_curl_health open-webui' "$ROOT/scripts/ods-doctor.sh"; then
        echo "doctor missing sr_curl_health for dashboard/open-webui" >&2
        FAIL=1
    fi
fi

# Governance artifacts present
for g in \
    "$ROOT/docs/ADR-HEALTH-PROBE-MERGE-ORDER.md" \
    "$ROOT/../.ai-rulez/config.toml" \
    "$ROOT/tests/test-service-manifest-health-contract-drift.sh"; do
    if [[ ! -f "$g" ]]; then
        echo "missing governance artifact: $g" >&2
        FAIL=1
    fi
done
ok "governance artifacts present"

if (( FAIL )); then
    echo "HEALTH_PROBE_SHADOW_AUDIT_FAIL" >&2
    exit 1
fi
echo "HEALTH_PROBE_SHADOW_AUDIT_OK"
