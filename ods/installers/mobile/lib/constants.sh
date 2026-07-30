#!/bin/bash
# Purpose: Shared constants for the Android Lite (Termux) installer and CLI.
# Expects: HOME set. May be sourced standalone (pure — no side effects).
# Provides: ODS_MOBILE_* paths, llama.cpp pin, Termux package list, CMake flags.
# Modder notes: Change LLAMA_CPP_ANDROID_TAG only after (1) confirming the tag
#   builds under the Termux toolchain — the termux-packages llama-cpp pin is a
#   good known-good signal — and (2) re-checking the CMake option names below
#   against that exact tag; llama.cpp renames options between releases
#   (LLAMA_CURL is already deprecated at b9934, LLAMA_BUILD_WEBUI became
#   LLAMA_BUILD_UI).

# Runtime layout — everything lives under one deletable directory in Termux HOME.
ODS_MOBILE_HOME="${ODS_MOBILE_HOME:-$HOME/.ods-mobile}"
ODS_MOBILE_MODELS_DIR="$ODS_MOBILE_HOME/models"
ODS_MOBILE_BIN_DIR="$ODS_MOBILE_HOME/bin"
ODS_MOBILE_LIB_DIR="$ODS_MOBILE_HOME/lib"
ODS_MOBILE_CONFIG_DIR="$ODS_MOBILE_HOME/config"
ODS_MOBILE_LOGS_DIR="$ODS_MOBILE_HOME/logs"
ODS_MOBILE_SRC_DIR="$ODS_MOBILE_HOME/llama.cpp"
ODS_MOBILE_ENV_FILE="$ODS_MOBILE_HOME/env"
ODS_MOBILE_CATALOG="$ODS_MOBILE_CONFIG_DIR/mobile-models.json"

# llama.cpp pin for the Android CPU path.
# b9934 is the tag the official termux-packages llama-cpp package builds with
# the Termux toolchain (checked 2026-07-19), and it builds clean on Linux CPU
# with the exact flag set below. Real-device validation checklist:
# docs/ANDROID-LITE.md.
LLAMA_CPP_ANDROID_TAG="b9934"
LLAMA_CPP_ANDROID_REPO="https://github.com/ggml-org/llama.cpp.git"

# Termux packages required to build and run. libandroid-spawn is required by
# llama.cpp on Termux (per upstream docs/android.md and the termux-packages
# build recipe). jq is used by the catalog/model tooling.
ODS_MOBILE_PACKAGES="git cmake clang make ninja curl jq libandroid-spawn"

# CPU-only, deterministic, offline-friendly build. Verified against tag b9934:
# - BUILD_SHARED_LIBS=OFF     → self-contained binaries we can copy to bin/
# - GGML_OPENMP=OFF           → matches the termux-packages known-good recipe
# - LLAMA_BUILD_UI=OFF + LLAMA_USE_PREBUILT_UI=OFF
#                             → skips the web-UI npm build AND the HF asset
#                               download (the prebuilt bundle for b9934 is
#                               missing loading.html and hard-fails the build)
# - LLAMA_OPENSSL=OFF         → no TLS dep; model downloads use our own curl
LLAMA_CPP_ANDROID_CMAKE_FLAGS="-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_OPENMP=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_OPENSSL=OFF"
LLAMA_CPP_ANDROID_TARGETS="llama-cli llama-server llama-bench"

# Conservative runtime defaults. Context is the primary Android memory control;
# 4096 keeps an 8B-class model workable on 12 GB phones. Override per command
# with --ctx or persistently in $ODS_MOBILE_ENV_FILE.
ODS_MOBILE_DEFAULT_CTX=4096
ODS_MOBILE_DEFAULT_HOST="127.0.0.1"
ODS_MOBILE_DEFAULT_PORT=8080

# Rough disk requirement for build tree + binaries + one model (KB, ~8 GB).
ODS_MOBILE_MIN_DISK_KB=8388608
