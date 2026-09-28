#!/usr/bin/env bash
# Resolve one qualified native model without evaluating registry data as shell.
# Both installers and everyday restarts validate before stopping a live model.
macos_resolve_checkpoint_args() {
    # Also resolves speculative-decoding flags and the macOS defaults
    # (--ctx-checkpoints 32, --spec-type ngram-mod) against the runtime's
    # --help. LLAMA_ARG_SPEC_TYPE itself is passed by the caller unchanged.
    # $3 is the --reasoning-format the caller mapped from LLAMA_REASONING. When
    # given, the caller leaves reasoning to this function: --reasoning on
    # runtimes that have it (b9014), else that --reasoning-format.
    local install_dir="$1" binary="$2" reasoning_format="${3:-}" interval checkpoints cache_mib idle_seconds min_spacing fields_file field
    local spec_type spec_default draft_n_max draft_type_k draft_type_v reasoning helper
    local -a reasoning_args=()
    MACOS_NATIVE_CHECKPOINT_ARGS=()
    interval="$(read_env_value "${install_dir}/.env" LLAMA_ARG_CHECKPOINT_EVERY_NT)"
    checkpoints="$(read_env_value "${install_dir}/.env" LLAMA_ARG_CTX_CHECKPOINTS)"
    cache_mib="$(read_env_value "${install_dir}/.env" LLAMA_ARG_CACHE_RAM)"
    idle_seconds="$(read_env_value "${install_dir}/.env" LLAMA_ARG_SLEEP_IDLE_SECONDS)"
    min_spacing="$(read_env_value "${install_dir}/.env" LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT)"
    spec_type="$(read_env_value "${install_dir}/.env" LLAMA_ARG_SPEC_TYPE)"
    spec_default="$(read_env_value "${install_dir}/.env" LLAMA_SPEC_TYPE)"
    draft_n_max="$(read_env_value "${install_dir}/.env" LLAMA_ARG_SPEC_DRAFT_N_MAX)"
    draft_type_k="$(read_env_value "${install_dir}/.env" LLAMA_ARG_SPEC_DRAFT_TYPE_K)"
    draft_type_v="$(read_env_value "${install_dir}/.env" LLAMA_ARG_SPEC_DRAFT_TYPE_V)"
    reasoning="$(read_env_value "${install_dir}/.env" LLAMA_REASONING)"
    helper="${install_dir}/installers/macos/lib/native-checkpoint-args.py"
    if [[ ! -f "$helper" ]]; then
        # Defaults are best effort; explicit settings must be qualified.
        if [[ -n "$interval$checkpoints$cache_mib$idle_seconds$min_spacing$draft_n_max$draft_type_k$draft_type_v" ]]; then
            echo "The native runtime tuning validator is missing. Repair the ODS installation." >&2
            return 1
        fi
        [[ -z "$reasoning_format" ]] || MACOS_NATIVE_CHECKPOINT_ARGS=(--reasoning-format "$reasoning_format")
        return 0
    fi
    [[ -z "$reasoning_format" ]] || reasoning_args=(--reasoning-mode="$reasoning" --reasoning-format-fallback="$reasoning_format")
    fields_file="$(mktemp)" || return 1
    if ! "${ODS_PYTHON_CMD:-python3}" "$helper" \
        --binary "$binary" --interval="$interval" --checkpoints="$checkpoints" --cache-mib="$cache_mib" \
        --idle-seconds="$idle_seconds" --min-spacing="$min_spacing" \
        --explicit-spec-type="$spec_type" --spec-default="$spec_default" --draft-n-max="$draft_n_max" \
        --draft-type-k="$draft_type_k" --draft-type-v="$draft_type_v" \
        ${reasoning_args[@]+"${reasoning_args[@]}"} --apply-defaults > "$fields_file"; then
        rm -f "$fields_file"
        return 1
    fi
    while IFS= read -r -d '' field; do MACOS_NATIVE_CHECKPOINT_ARGS+=("$field"); done < "$fields_file"
    rm -f "$fields_file"
}

macos_model_store_compose_flags() {
    local flags="$1" helper="${INSTALL_DIR}/scripts/model-store-compose-flags.py"
    if [[ ! -e "${INSTALL_DIR}/.model-stores.compose.json" && ! -e "${INSTALL_DIR}/data/model-stores.json" ]]; then
        printf '%s\n' "$flags"
        return
    fi
    if [[ ! -f "$helper" ]]; then
        echo "The registered model-store Compose resolver is missing. Repair the ODS installation." >&2
        return 1
    fi
    python3 "$helper" --install-dir "$INSTALL_DIR" --flags="$flags" --format flags
}

macos_resolve_native_model() {
    local install_dir="$1" default_binary="$2" default_context="$3" allow_missing_default="${4:-false}"
    local resolver="${install_dir}/scripts/resolve-model-store.py"
    MACOS_NATIVE_MODEL_PATH=""
    MACOS_NATIVE_BINARY="$default_binary"
    MACOS_NATIVE_CONTEXT="$default_context"
    MACOS_NATIVE_PROFILE=false
    MACOS_NATIVE_PROFILE_ARGS=()

    if [[ ! -f "$resolver" ]]; then
        local store filename
        store="$(read_env_value "${install_dir}/.env" ODS_ACTIVE_MODEL_STORE)"
        if [[ -n "$store" && "$store" != default ]] || [[ -f "${install_dir}/data/model-stores.json" ]]; then
            echo "The active model store requires the installed model resolver. Update ODS before starting it." >&2
            return 1
        fi
        filename="$(read_env_value "${install_dir}/.env" GGUF_FILE)"
        filename="${filename:-Qwen3.5-9B-Q4_K_M.gguf}"
        [[ "$filename" != */* && "$filename" != *\\* && "$filename" != . && "$filename" != .. ]] || return 1
        MACOS_NATIVE_MODEL_PATH="${install_dir}/data/models/${filename}"
    else
        local selection fields_file
        if ! selection="$(python3 "$resolver" --install-dir "$install_dir" --verify-artifacts)"; then
            echo "The selected model/runtime could not be verified. The running model has not been stopped." >&2
            return 1
        fi
        fields_file="$(mktemp)" || return 1
        # NUL framing preserves spaces and shell metacharacters as literal args.
        if ! printf '%s' "$selection" | python3 -c '
import json, os, sys
from pathlib import Path
try:
    value = json.load(sys.stdin)
    if value.get("schemaVersion") != 1 or value.get("available") is not True:
        raise ValueError("The selected model is unavailable")
    model = value["modelPath"]
    profile = value.get("profile")
    fields = [model]
    if profile is None:
        fields += ["", "", "false"]
    else:
        if profile.get("backend") not in {"metal", "cpu"}:
            raise ValueError("The registered runtime is not compatible with native macOS")
        binary = profile["executable"]
        context = profile["contextLength"]
        args = profile["args"]
        if type(context) is not int or not 4096 <= context <= 262144 or not isinstance(args, list):
            raise ValueError("Invalid native runtime profile")
        if not Path(binary).is_absolute() or not os.access(binary, os.X_OK):
            raise ValueError("The registered native runtime is not executable")
        with open(binary, "rb") as source:
            if source.read(4) not in (bytes.fromhex(v) for v in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")):
                raise ValueError("The registered runtime is not a macOS executable")
        fields += [binary, str(context), "true", *args]
    if not isinstance(model, str) or not Path(model).is_absolute():
        raise ValueError("Invalid selected model path")
    if any(not isinstance(v, str) or any(c in v for c in "\x00\n\r") for v in fields):
        raise ValueError("Invalid native model launch data")
    sys.stdout.buffer.write(b"\x00".join(v.encode() for v in fields) + b"\x00")
except (ValueError, KeyError, TypeError, OSError) as error:
    print(str(error), file=sys.stderr)
    sys.exit(1)
' > "$fields_file"; then
            rm -f "$fields_file"
            return 1
        fi
        local -a fields=()
        local field
        while IFS= read -r -d '' field; do fields+=("$field"); done < "$fields_file"
        rm -f "$fields_file"
        [[ "${#fields[@]}" -ge 4 ]] || return 1
        MACOS_NATIVE_MODEL_PATH="${fields[0]}"
        MACOS_NATIVE_BINARY="${fields[1]:-$default_binary}"
        MACOS_NATIVE_CONTEXT="${fields[2]:-$default_context}"
        MACOS_NATIVE_PROFILE="${fields[3]}"
        MACOS_NATIVE_PROFILE_ARGS=("${fields[@]:4}")
    fi
    if [[ ! -f "$MACOS_NATIVE_MODEL_PATH" ]] || { [[ ! -x "$MACOS_NATIVE_BINARY" ]] && [[ "$MACOS_NATIVE_PROFILE" == true || "$allow_missing_default" != true ]]; }; then
        echo "The selected model or native runtime is missing. Reconnect its drive or repair the installation before starting it." >&2
        return 1
    fi
}
