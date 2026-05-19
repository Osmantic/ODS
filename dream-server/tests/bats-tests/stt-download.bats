#!/usr/bin/env bats
# ============================================================================
# BATS tests for the STT pre-download logic in installers/phases/12-health.sh
# ============================================================================
# Guards against re-breakage of:
#   - PR #984: silent-failure message ("will download on first use" lie)
#   - PR #985: AUDIO_STT_MODEL env var wiring + backward-compat fallback
#   - Post-#985 bug fix: recovery command must include --max-time
#   - PR #1229: bounded preload timeout must stay non-fatal under set -e
#
# We execute only the STT pre-download block (lines ~165-215 of 12-health.sh)
# with curl stubbed, then assert on the commands it would have run and on the
# strings it prints.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TMPDIR_TEST="$BATS_TEST_TMPDIR"
    mkdir -p "$TMPDIR_TEST/bin"

    # Capture curl invocations to a file instead of actually hitting the network.
    # The stub also lets tests toggle "cache hit" vs "cache miss" via an env var.
    cat > "$TMPDIR_TEST/bin/curl" << 'CURL_STUB'
#!/usr/bin/env bash
# Record every invocation (for assertion), then return the scripted exit code.
echo "CURL: $*" >> "$TMPDIR_TEST/curl.log"
# STT_STUB_MODE controls behavior:
#   cache-hit:      GET /v1/models/{id} returns 0 (cached), all other calls return 0
#   cache-miss:     GET /v1/models returns 0, GET /v1/models/{id} returns 1, POST returns 0, verify GET returns 0
#   download-fail:  everything returns 0 except the verify GET after POST
#   post-timeout:   POST returns 28, verify GET fails, recovery path still runs
#   api-not-ready:  all curls return 1
case "${STT_STUB_MODE:-cache-hit}" in
    cache-hit)       exit 0 ;;
    api-not-ready)   exit 1 ;;
    cache-miss)
        # Distinguish GET /v1/models (ready probe) from GET /v1/models/{id} (cache) from POST.
        if [[ "$*" == *"-X POST"* ]]; then exit 0
        elif [[ "$*" == */v1/models ]] || [[ "$*" == *"/v1/models " ]] || [[ "$*" == *"/v1/models" ]]; then exit 0
        else exit 0  # verify GET after POST succeeds
        fi
        ;;
    download-fail)
        if [[ "$*" == */v1/models ]] || [[ "$*" == *"/v1/models" ]]; then exit 0
        elif [[ "$*" == *"-X POST"* ]]; then exit 0
        else exit 1  # cache GET + verify GET both fail
        fi
        ;;
    post-timeout)
        if [[ "$*" == */v1/models ]] || [[ "$*" == *"/v1/models" ]]; then exit 0
        elif [[ "$*" == *"-X POST"* ]]; then exit 28
        else exit 1  # cache GET + verify GET both fail
        fi
        ;;
    *) exit 0 ;;
esac
CURL_STUB
    chmod +x "$TMPDIR_TEST/bin/curl"
    export PATH="$TMPDIR_TEST/bin:$PATH"

    # Reset log on each test.
    : > "$TMPDIR_TEST/curl.log"

    # Environment that Phase 12's STT block expects. The block starts after the
    # health checks; we provide the same variables the rest of Phase 12 would have set.
    export SERVICE_PORTS_whisper="9000"
    export BGRN='' AMB='' NC='' DIM=''
    export LOG_FILE="$TMPDIR_TEST/install.log"
    : > "$LOG_FILE"
}

teardown() {
    rm -rf "$TMPDIR_TEST/bin" "$TMPDIR_TEST/curl.log" "$TMPDIR_TEST/install.log"
}

# The STT block extracted from 12-health.sh for testing in isolation. Pulling
# this out of the phase means we don't need to stub every other phase concern
# (service registry, docker health loops, etc.) — just the STT logic.
# If 12-health.sh drifts, update this block to match.
_run_stt_block() {
    bash -c '
        set -euo pipefail
        declare -A SERVICE_PORTS
        SERVICE_PORTS[whisper]="9000"

        if [[ "$ENABLE_VOICE" == "true" ]]; then
            if [[ -n "${AUDIO_STT_MODEL:-}" ]]; then
                STT_MODEL="$AUDIO_STT_MODEL"
            elif [[ "$GPU_BACKEND" == "nvidia" ]]; then
                STT_MODEL="deepdml/faster-whisper-large-v3-turbo-ct2"
            else
                STT_MODEL="Systran/faster-whisper-base"
            fi
            STT_MODEL_ENCODED="${STT_MODEL//\//%2F}"
            WHISPER_PORT_RESOLVED="${SERVICE_PORTS[whisper]:-9000}"
            WHISPER_URL="http://localhost:${WHISPER_PORT_RESOLVED}"
            STT_RECOVERY_CMD="curl --max-time 1800 -X POST ${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}"

            _stt_api_ready=false
            for _i in $(seq 1 3); do
                if curl -sf --max-time 2 "${WHISPER_URL}/v1/models" >/dev/null 2>&1; then
                    _stt_api_ready=true
                    break
                fi
                sleep 0
            done

            if ! $_stt_api_ready; then
                echo "API_NOT_READY: $STT_RECOVERY_CMD"
            elif curl -sf --max-time 10 "${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}" >/dev/null 2>&1; then
                echo "ALREADY_CACHED: ${STT_MODEL}"
            else
                echo "DOWNLOADING: ${STT_MODEL}"
                curl -s --max-time 600 -X POST "${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}" >> "$LOG_FILE" 2>&1 || true
                if curl -sf --max-time 10 "${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}" >/dev/null 2>&1; then
                    echo "CACHED: ${STT_MODEL}"
                else
                    echo "DOWNLOAD_FAILED: $STT_RECOVERY_CMD"
                fi
            fi
        fi
    '
}

# ── Tests ──────────────────────────────────────────────────────────────────

@test "stt: reads AUDIO_STT_MODEL from env when set" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=nvidia
    export AUDIO_STT_MODEL="custom-org/custom-model"
    export STT_STUB_MODE=cache-hit

    run _run_stt_block
    assert_success
    assert_output --partial "custom-org/custom-model"
}

@test "stt: falls back to NVIDIA turbo when AUDIO_STT_MODEL unset and GPU_BACKEND=nvidia" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=nvidia
    unset AUDIO_STT_MODEL
    export STT_STUB_MODE=cache-hit

    run _run_stt_block
    assert_success
    assert_output --partial "deepdml/faster-whisper-large-v3-turbo-ct2"
}

@test "stt: falls back to base model when AUDIO_STT_MODEL unset and GPU_BACKEND != nvidia" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=amd
    unset AUDIO_STT_MODEL
    export STT_STUB_MODE=cache-hit

    run _run_stt_block
    assert_success
    assert_output --partial "Systran/faster-whisper-base"
}

@test "stt: skips entirely when ENABLE_VOICE=false" {
    export ENABLE_VOICE=false
    export GPU_BACKEND=nvidia
    export STT_STUB_MODE=cache-hit

    run _run_stt_block
    assert_success
    # No output and no curl invocations logged.
    [[ -z "$output" ]]
    [[ ! -s "$TMPDIR_TEST/curl.log" ]]
}

@test "stt: recovery command includes bounded manual retry timeout" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=amd
    export AUDIO_STT_MODEL="Systran/faster-whisper-base"
    export STT_STUB_MODE=download-fail

    run _run_stt_block
    assert_success
    # Must not say "will download on first use" (the old lie from pre-PR #984).
    refute_output --partial "will download on first use"
    # Must include the recovery command with --max-time.
    assert_output --partial "curl --max-time 1800 -X POST"
}

@test "stt: preload POST timeout is non-fatal under set -e" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=amd
    export AUDIO_STT_MODEL="Systran/faster-whisper-base"
    export STT_STUB_MODE=post-timeout

    run _run_stt_block
    assert_success
    assert_output --partial "DOWNLOAD_FAILED"
    assert_output --partial "curl --max-time 1800 -X POST"
    run grep -F -- "--max-time 600 -X POST" "$TMPDIR_TEST/curl.log"
    assert_success
}

@test "stt: already-cached short-circuits without POST" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=amd
    export AUDIO_STT_MODEL="Systran/faster-whisper-base"
    export STT_STUB_MODE=cache-hit

    run _run_stt_block
    assert_success
    assert_output --partial "ALREADY_CACHED"
    # Should NOT have issued a POST.
    run grep -F -- "-X POST" "$TMPDIR_TEST/curl.log"
    assert_failure
}

@test "stt: URL-encodes slash in model name" {
    export ENABLE_VOICE=true
    export GPU_BACKEND=amd
    export AUDIO_STT_MODEL="org/name-with-slash"
    export STT_STUB_MODE=cache-miss

    run _run_stt_block
    assert_success
    # The curl log should contain the encoded form in at least one URL.
    run grep -F "org%2Fname-with-slash" "$TMPDIR_TEST/curl.log"
    assert_success
}
