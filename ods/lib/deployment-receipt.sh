#!/usr/bin/env bash
# Purpose: record and compare the last fully reconciled Docker Compose deployment.
# Expects: docker, jq, INSTALL_DIR-compatible paths, and resolved compose flags.
# Provides: ods_deployment_receipt_write, ods_deployment_attestation_json.

ods_deployment_receipt_path() {
    printf '%s/data/deployment-receipt.json\n' "$1"
}

_ods_deployment_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
        return
    fi
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 | awk '{print $1}'
        return
    fi
    return 1
}

_ods_deployment_plan_hash() {
    local install_dir="$1"
    shift
    local rendered
    if ! rendered=$(cd "$install_dir" && docker compose "$@" config --format json); then
        return 1
    fi
    printf '%s' "$rendered" | _ods_deployment_sha256
}

_ods_deployment_runtime_hash() {
    local install_dir="$1"
    shift
    local ids="" rows="" id="" inspected=""
    if ! ids=$(cd "$install_dir" \
        && docker compose "$@" ps --all --format '{{.ID}}'); then
        return 1
    fi

    while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        if ! inspected=$(docker inspect --format \
            '{{.Id}}|{{.Image}}|{{index .Config.Labels "com.docker.compose.service"}}' \
            "$id"); then
            return 1
        fi
        rows+="${inspected}"$'\n'
    done <<< "$ids"

    printf '%s' "$rows" | LC_ALL=C sort | _ods_deployment_sha256
}

ods_deployment_receipt_write() {
    local install_dir="$1" action="$2"
    shift 2
    local plan_hash runtime_hash receipt_path source_revision=""
    plan_hash=$(_ods_deployment_plan_hash "$install_dir" "$@") || return 1
    runtime_hash=$(_ods_deployment_runtime_hash "$install_dir" "$@") || return 1
    receipt_path=$(ods_deployment_receipt_path "$install_dir")
    if command -v git >/dev/null 2>&1; then
        source_revision=$(git -C "$install_dir" rev-parse HEAD 2>/dev/null || true)
    fi

    mkdir -p "$(dirname "$receipt_path")"
    local tmp_file
    tmp_file=$(mktemp "${receipt_path}.tmp.XXXXXX")
    jq -n \
        --arg schema "ods.deployment-receipt.v1" \
        --arg state "applied" \
        --arg action "$action" \
        --arg planSha256 "$plan_hash" \
        --arg runtimeSha256 "$runtime_hash" \
        --arg sourceRevision "$source_revision" \
        --arg recordedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg composeFlags "$*" \
        '{schema:$schema, state:$state, action:$action,
          planSha256:$planSha256, runtimeSha256:$runtimeSha256,
          sourceRevision:$sourceRevision, recordedAt:$recordedAt,
          composeFlags:$composeFlags}' > "$tmp_file"
    chmod 600 "$tmp_file"
    mv -f "$tmp_file" "$receipt_path"
}

ods_deployment_attestation_json() {
    local install_dir="$1"
    shift
    local receipt_path
    receipt_path=$(ods_deployment_receipt_path "$install_dir")
    if [[ ! -f "$receipt_path" ]]; then
        jq -n '{state:"missing", message:"No successful full-stack deployment receipt is available."}'
        return
    fi
    if ! jq -e '
        .schema == "ods.deployment-receipt.v1"
        and .state == "applied"
        and (.planSha256 | type == "string" and length == 64)
        and (.runtimeSha256 | type == "string" and length == 64)
    ' "$receipt_path" >/dev/null 2>&1; then
        jq -n '{state:"invalid_receipt", message:"The deployment receipt is invalid."}'
        return
    fi

    local expected_plan expected_runtime current_plan current_runtime action recorded_at
    expected_plan=$(jq -r '.planSha256' "$receipt_path")
    expected_runtime=$(jq -r '.runtimeSha256' "$receipt_path")
    action=$(jq -r '.action' "$receipt_path")
    recorded_at=$(jq -r '.recordedAt' "$receipt_path")
    if ! current_plan=$(_ods_deployment_plan_hash "$install_dir" "$@"); then
        jq -n \
            --arg action "$action" --arg recordedAt "$recorded_at" \
            '{state:"unavailable", action:$action, recordedAt:$recordedAt,
              message:"The current Compose plan could not be rendered."}'
        return
    fi
    if [[ "$current_plan" != "$expected_plan" ]]; then
        jq -n \
            --arg action "$action" --arg recordedAt "$recorded_at" \
            --arg expected "$expected_plan" --arg current "$current_plan" \
            '{state:"desired_config_changed", action:$action, recordedAt:$recordedAt,
              expectedPlanSha256:$expected, currentPlanSha256:$current,
              message:"The desired Compose plan changed after the last full reconciliation."}'
        return
    fi

    if ! current_runtime=$(_ods_deployment_runtime_hash "$install_dir" "$@"); then
        jq -n \
            --arg action "$action" --arg recordedAt "$recorded_at" \
            '{state:"unavailable", action:$action, recordedAt:$recordedAt,
              message:"The running Compose resources could not be inspected."}'
        return
    fi
    if [[ "$current_runtime" != "$expected_runtime" ]]; then
        jq -n \
            --arg action "$action" --arg recordedAt "$recorded_at" \
            --arg expected "$expected_runtime" --arg current "$current_runtime" \
            '{state:"runtime_changed", action:$action, recordedAt:$recordedAt,
              expectedRuntimeSha256:$expected, currentRuntimeSha256:$current,
              message:"Compose container or image identity changed after the last full reconciliation."}'
        return
    fi

    jq -n \
        --arg action "$action" --arg recordedAt "$recorded_at" \
        --arg planSha256 "$current_plan" --arg runtimeSha256 "$current_runtime" \
        '{state:"in_sync", action:$action, recordedAt:$recordedAt,
          planSha256:$planSha256, runtimeSha256:$runtimeSha256,
          message:"Desired Compose plan and running resource identities match the receipt."}'
}
