#!/bin/bash
# Purpose: Android Lite (Termux) installer — native CPU llama.cpp runtime,
#   verified model download, and the ods-mobile CLI. No Docker, no compose,
#   none of the desktop stack.
# Expects: run inside Termux on aarch64. --dry-run runs anywhere with
#   ODS_PLATFORM_OVERRIDE=android-termux and performs no side effects.
# Provides: $ODS_MOBILE_HOME runtime tree, $PREFIX/bin/ods-mobile.
# Modder notes: phases are functions run in order; each sets INSTALL_PHASE for
#   the ERR trap. Model download/verify lives in lib/model-pull.sh (shared with
#   the ods-mobile CLI — change it there, not here). iOS a-Shell intentionally
#   still exits 1: Android Lite is Termux-only in this iteration.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$SCRIPT_DIR/installers/common.sh"
source "$SCRIPT_DIR/installers/mobile/lib/constants.sh"
source "$SCRIPT_DIR/installers/mobile/lib/model-pull.sh"

INSTALL_PHASE="init"
trap 'echo "[ERROR] Android Lite install failed during phase: $INSTALL_PHASE" >&2' ERR

CATALOG_SRC="$SCRIPT_DIR/config/mobile-models.json"
CLI_SRC="$SCRIPT_DIR/installers/mobile/ods-mobile"

DRY_RUN=false
SKIP_MODEL=false
REBUILD=false
MODEL_ID=""

usage() {
    cat <<'EOF'
Usage: install-mobile.sh [options]

Experimental Android Lite installer (Termux only).

Options:
  --dry-run        Print the planned phases without changing anything.
  --model <id>     Install a specific catalog model (default: catalog default).
  --skip-model     Install runtime + CLI only; pull models later with
                   'ods-mobile models pull <id>'.
  --rebuild        Force a fresh llama.cpp checkout and build.
  -h, --help       Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --skip-model) SKIP_MODEL=true ;;
        --rebuild) REBUILD=true ;;
        --model)
            shift
            MODEL_ID="${1:?--model requires a catalog model id}"
            ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "[ERROR] Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

# ── Platform gate ───────────────────────────────────────────────────────────
INSTALL_PHASE="platform-gate"
platform="$(detect_platform)"
case "$platform" in
    android-termux)
        echo "[INFO] ODS detected mobile platform: $platform"
        ;;
    ios-ashell)
        echo "[ERROR] iOS a-Shell is not yet supported." >&2
        echo "        Android Lite is Termux-only in this iteration." >&2
        exit 1
        ;;
    *)
        echo "[ERROR] install-mobile.sh only handles Android Termux." >&2
        echo "        (CI dry-runs: set ODS_PLATFORM_OVERRIDE=android-termux" >&2
        echo "        and pass --dry-run.)" >&2
        exit 1
        ;;
esac

phase_preflight() {
    INSTALL_PHASE="preflight"
    if $DRY_RUN; then
        echo "[dry-run] preflight: would require aarch64, writable \$PREFIX/bin,"
        echo "[dry-run]   and >= $((ODS_MOBILE_MIN_DISK_KB / 1024 / 1024)) GB free in \$HOME."
        return 0
    fi

    local arch
    arch="$(uname -m)"
    if [[ "$arch" != "aarch64" ]]; then
        echo "[ERROR] Android Lite requires aarch64, found: $arch" >&2
        exit 1
    fi

    if [[ -z "${PREFIX:-}" || ! -d "$PREFIX/bin" || ! -w "$PREFIX/bin" ]]; then
        echo "[ERROR] \$PREFIX/bin is missing or not writable." >&2
        echo "        Android Lite must run inside Termux (https://termux.dev)." >&2
        exit 1
    fi

    local avail_kb
    avail_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
    if [[ "$avail_kb" -lt "$ODS_MOBILE_MIN_DISK_KB" ]]; then
        echo "[ERROR] Need ~$((ODS_MOBILE_MIN_DISK_KB / 1024 / 1024)) GB free in \$HOME, found $((avail_kb / 1024 / 1024)) GB." >&2
        exit 1
    fi

    echo "[WARN] Android 12+ aggressively kills background processes (phantom"
    echo "[WARN] process killer). For long builds or serving, acquire a wake"
    echo "[WARN] lock first: termux-wake-lock. See docs/ANDROID-LITE.md."
}

phase_packages() {
    INSTALL_PHASE="packages"
    if $DRY_RUN; then
        echo "[dry-run] packages: would run: pkg install -y $ODS_MOBILE_PACKAGES"
        return 0
    fi
    # shellcheck disable=SC2086
    pkg install -y $ODS_MOBILE_PACKAGES
}

# Runs AFTER phase_packages: a fresh Termux has no jq until then, so nothing
# before this point may parse the catalog.
phase_resolve_model() {
    INSTALL_PHASE="model-selection"
    if $DRY_RUN && ! command -v jq >/dev/null 2>&1; then
        # Fresh-host dry-run: jq is not installed yet (phase_packages would
        # install it), so resolution is only described, never executed.
        MODEL_ID="${MODEL_ID:-(catalog default)}"
        MODEL_FILE="(resolved from catalog once jq is installed)"
        MODEL_CTX_DEFAULT="$ODS_MOBILE_DEFAULT_CTX"
        echo "[dry-run] model-selection: would resolve '$MODEL_ID' from $CATALOG_SRC"
        echo "[dry-run]   (jq not present yet; installed by the packages phase)."
        return 0
    fi

    if [[ -z "$MODEL_ID" ]]; then
        MODEL_ID="$(jq -re '.default_model' "$CATALOG_SRC")"
    fi
    # Fails loudly if the id is not in the catalog:
    MODEL_FILE="$(ods_mobile_model_field "$CATALOG_SRC" "$MODEL_ID" gguf_file)"
    MODEL_MIN_RAM_GB="$(ods_mobile_model_field "$CATALOG_SRC" "$MODEL_ID" min_ram_gb)"
    MODEL_CTX_DEFAULT="$(ods_mobile_model_field "$CATALOG_SRC" "$MODEL_ID" context_default)"

    if $DRY_RUN; then
        echo "[dry-run] model-selection: resolved '$MODEL_ID' ($MODEL_FILE) from catalog;"
        echo "[dry-run]   would warn below ${MODEL_MIN_RAM_GB} GB RAM."
        return 0
    fi

    local mem_kb mem_gb
    mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
    mem_gb=$((mem_kb / 1024 / 1024))
    echo "[INFO] Detected ~${mem_gb} GB RAM."
    if [[ "$mem_gb" -lt "$MODEL_MIN_RAM_GB" ]]; then
        echo "[WARN] Model '$MODEL_ID' targets ${MODEL_MIN_RAM_GB} GB+ RAM devices."
        echo "[WARN] It may be killed by Android's low-memory killer here."
        echo "[WARN] Consider: --model qwen3.5-2b-q4 (low-RAM fallback)."
    fi
}

phase_build() {
    INSTALL_PHASE="build-llama-cpp"
    if $DRY_RUN; then
        echo "[dry-run] build: would clone $LLAMA_CPP_ANDROID_REPO at tag $LLAMA_CPP_ANDROID_TAG"
        echo "[dry-run]   into $ODS_MOBILE_SRC_DIR and build (CPU-only):"
        echo "[dry-run]   cmake -B build $LLAMA_CPP_ANDROID_CMAKE_FLAGS"
        echo "[dry-run]   cmake --build build --config Release --target $LLAMA_CPP_ANDROID_TARGETS"
        return 0
    fi

    local recorded_tag=""
    if [[ -f "$ODS_MOBILE_ENV_FILE" ]]; then
        recorded_tag="$(awk -F= '/^ODS_MOBILE_LLAMA_TAG=/ {print $2}' "$ODS_MOBILE_ENV_FILE")"
    fi
    if [[ -x "$ODS_MOBILE_BIN_DIR/llama-cli" && "$recorded_tag" == "$LLAMA_CPP_ANDROID_TAG" ]] && ! $REBUILD; then
        LLAMA_COMMIT="$(awk -F= '/^ODS_MOBILE_LLAMA_COMMIT=/ {print $2}' "$ODS_MOBILE_ENV_FILE")"
        echo "[INFO] llama.cpp $LLAMA_CPP_ANDROID_TAG already built — skipping (use --rebuild to force)."
        return 0
    fi

    if [[ -d "$ODS_MOBILE_SRC_DIR" ]]; then
        if $REBUILD; then
            echo "[INFO] --rebuild: removing previous checkout $ODS_MOBILE_SRC_DIR"
            rm -rf "$ODS_MOBILE_SRC_DIR"
        else
            echo "[ERROR] $ODS_MOBILE_SRC_DIR exists but does not match tag $LLAMA_CPP_ANDROID_TAG." >&2
            echo "        Re-run with --rebuild to replace it." >&2
            exit 1
        fi
    fi

    echo "[INFO] Cloning llama.cpp @ $LLAMA_CPP_ANDROID_TAG (shallow) ..."
    git clone --depth 1 --branch "$LLAMA_CPP_ANDROID_TAG" \
        "$LLAMA_CPP_ANDROID_REPO" "$ODS_MOBILE_SRC_DIR"

    echo "[INFO] Building llama.cpp (CPU). This takes a while on a phone — keep"
    echo "[INFO] the screen on or hold a wake lock (termux-wake-lock)."
    # shellcheck disable=SC2086
    cmake -S "$ODS_MOBILE_SRC_DIR" -B "$ODS_MOBILE_SRC_DIR/build" $LLAMA_CPP_ANDROID_CMAKE_FLAGS
    # shellcheck disable=SC2086
    cmake --build "$ODS_MOBILE_SRC_DIR/build" --config Release -j"$(nproc)" \
        --target $LLAMA_CPP_ANDROID_TARGETS

    mkdir -p "$ODS_MOBILE_BIN_DIR"
    local target
    for target in $LLAMA_CPP_ANDROID_TARGETS; do
        cp "$ODS_MOBILE_SRC_DIR/build/bin/$target" "$ODS_MOBILE_BIN_DIR/$target"
    done
    LLAMA_COMMIT="$(git -C "$ODS_MOBILE_SRC_DIR" rev-parse HEAD)"
    echo "[INFO] Built $LLAMA_CPP_ANDROID_TAG ($LLAMA_COMMIT)."
    echo "[INFO] Binaries are self-contained; you may delete $ODS_MOBILE_SRC_DIR"
    echo "[INFO] later to reclaim disk space."
}

phase_model() {
    INSTALL_PHASE="model-download"
    if $SKIP_MODEL; then
        echo "[INFO] --skip-model: skipping model download."
        echo "[INFO] Pull later with: ods-mobile models pull $MODEL_ID"
        return 0
    fi
    if $DRY_RUN; then
        echo "[dry-run] model: would download $MODEL_FILE for '$MODEL_ID'"
        echo "[dry-run]   into $ODS_MOBILE_MODELS_DIR and verify its pinned sha256."
        return 0
    fi
    mkdir -p "$ODS_MOBILE_MODELS_DIR"
    ods_mobile_pull_model "$CATALOG_SRC" "$MODEL_ID" "$ODS_MOBILE_MODELS_DIR"
}

phase_configure() {
    INSTALL_PHASE="configure"
    if $DRY_RUN; then
        echo "[dry-run] configure: would create $ODS_MOBILE_HOME/{models,bin,lib,config,logs},"
        echo "[dry-run]   install the mobile catalog + model-pull lib there,"
        echo "[dry-run]   write $ODS_MOBILE_ENV_FILE (model=$MODEL_ID ctx=$MODEL_CTX_DEFAULT"
        echo "[dry-run]   host=$ODS_MOBILE_DEFAULT_HOST port=$ODS_MOBILE_DEFAULT_PORT),"
        echo "[dry-run]   and install ods-mobile to \$PREFIX/bin/ods-mobile."
        return 0
    fi

    mkdir -p "$ODS_MOBILE_MODELS_DIR" "$ODS_MOBILE_BIN_DIR" "$ODS_MOBILE_LIB_DIR" \
             "$ODS_MOBILE_CONFIG_DIR" "$ODS_MOBILE_LOGS_DIR"

    cp "$CATALOG_SRC" "$ODS_MOBILE_CATALOG"
    cp "$SCRIPT_DIR/installers/mobile/lib/model-pull.sh" "$ODS_MOBILE_LIB_DIR/model-pull.sh"

    cat > "$ODS_MOBILE_ENV_FILE" <<EOF
# Generated by install-mobile.sh — edit freely; regenerated on reinstall.
ODS_MOBILE_MODEL=$MODEL_ID
ODS_MOBILE_CTX=$MODEL_CTX_DEFAULT
ODS_MOBILE_HOST=$ODS_MOBILE_DEFAULT_HOST
ODS_MOBILE_PORT=$ODS_MOBILE_DEFAULT_PORT
ODS_MOBILE_LLAMA_TAG=$LLAMA_CPP_ANDROID_TAG
ODS_MOBILE_LLAMA_COMMIT=${LLAMA_COMMIT:-unknown}
EOF

    install -m 755 "$CLI_SRC" "$PREFIX/bin/ods-mobile"
    echo "[INFO] Installed ods-mobile to \$PREFIX/bin/ods-mobile"
}

phase_summary() {
    INSTALL_PHASE="summary"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo " ODS Android Lite (experimental) — Termux, CPU-only"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo " llama.cpp: $LLAMA_CPP_ANDROID_TAG"
    echo " model:     $MODEL_ID ($MODEL_FILE)$($SKIP_MODEL && echo ' [skipped — pull later]')"
    echo " context:   $MODEL_CTX_DEFAULT (override: --ctx)"
    echo " state:     $ODS_MOBILE_HOME"
    echo ""
    echo " Next steps:"
    echo "   ods-mobile status"
    echo "   ods-mobile chat"
    echo "   ods-mobile serve     # OpenAI-compatible API on $ODS_MOBILE_DEFAULT_HOST:$ODS_MOBILE_DEFAULT_PORT"
    echo "   ods-mobile bench     # record provenance before quoting any numbers"
    echo ""
    echo " This profile is experimental. Performance on phones is unmeasured"
    echo " until real-device benchmarks are recorded (docs/ANDROID-LITE.md)."
    if $DRY_RUN; then
        echo ""
        echo " (dry-run: nothing was installed or downloaded)"
    fi
}

# Order matters: packages before jq-dependent model resolution (fresh Termux
# has no jq), and configure before the model pull so a failed download still
# leaves a working CLI ('ods-mobile models pull <id>' recovers).
phase_preflight
phase_packages
phase_resolve_model
phase_build
phase_configure
phase_model
phase_summary
