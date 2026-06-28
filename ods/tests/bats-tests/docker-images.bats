#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/docker-images.sh
# ============================================================================
# Tests: docker_image_available(), validate_docker_image_or_fallback()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export LOG_FILE="$BATS_TEST_TMPDIR/docker-images.log"
    touch "$LOG_FILE"

    ai() { echo "AI: $*"; }
    ai_ok() { echo "OK: $*"; }
    ai_warn() { echo "WARN: $*"; }
    ai_bad() { echo "BAD: $*"; }
    export -f ai ai_ok ai_warn ai_bad

    export DOCKER_CMD="$BATS_TEST_TMPDIR/docker"
    export DOCKER_IMAGE_CHECK_TIMEOUT=2
    unset LLAMA_SERVER_IMAGE_FALLBACK

    cat > "$DOCKER_CMD" <<'MOCK'
#!/usr/bin/env bash
case "$*" in
  "image inspect local/image:present")
    exit 0
    ;;
  "image inspect remote/image:present")
    exit 1
    ;;
  "manifest inspect remote/image:present")
    exit 0
    ;;
  "image inspect missing/image:tag")
    exit 1
    ;;
  "manifest inspect missing/image:tag")
    echo "manifest unknown" >&2
    exit 1
    ;;
  "image inspect fallback/image:present")
    exit 1
    ;;
  "manifest inspect fallback/image:present")
    exit 0
    ;;
  *)
    echo "unexpected docker call: $*" >&2
    exit 99
    ;;
esac
MOCK
    chmod +x "$DOCKER_CMD"

    source "$BATS_TEST_DIRNAME/../../installers/lib/docker-images.sh"
}

@test "docker_image_available: accepts locally inspected image" {
    run docker_image_available "local/image:present"
    assert_success
}

@test "docker_image_available: accepts remotely resolvable manifest" {
    run docker_image_available "remote/image:present"
    assert_success
}

@test "docker_image_available: rejects missing manifest" {
    run docker_image_available "missing/image:tag"
    assert_failure
}

@test "validate_docker_image_or_fallback: returns original image when available" {
    call_validate_original() {
        local selected=""
        validate_docker_image_or_fallback selected "remote/image:present" "test-image" "LLAMA_SERVER_IMAGE_FALLBACK"
        echo "selected=$selected"
    }

    run call_validate_original
    assert_success
    assert_output --partial "test-image image available: remote/image:present"
    assert_output --partial "selected=remote/image:present"
}

@test "validate_docker_image_or_fallback: uses explicit fallback when configured" {
    export LLAMA_SERVER_IMAGE_FALLBACK="fallback/image:present"

    call_validate_fallback() {
        local selected=""
        validate_docker_image_or_fallback selected "missing/image:tag" "test-image" "LLAMA_SERVER_IMAGE_FALLBACK"
        echo "selected=$selected"
    }

    run call_validate_fallback
    assert_success
    assert_output --partial "Trying explicit fallback from LLAMA_SERVER_IMAGE_FALLBACK"
    assert_output --partial "selected=fallback/image:present"
}

@test "validate_docker_image_or_fallback: fails without implicit substitution" {
    call_validate_missing() {
        local selected=""
        validate_docker_image_or_fallback selected "missing/image:tag" "test-image" "LLAMA_SERVER_IMAGE_FALLBACK" || return $?
        echo "selected=$selected"
    }

    run call_validate_missing
    assert_failure
    assert_output --partial "test-image image is unavailable: missing/image:tag"
    assert_output --partial "set LLAMA_SERVER_IMAGE to a valid image"
}
