#!/usr/bin/env bash
# Regression coverage for Windows NVIDIA installs whose driver can run the
# llama.cpp CUDA image but not Speaches' newer CUDA Whisper image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/lib"
cp "$ROOT_DIR/lib/python-cmd.sh" "$tmp/lib/python-cmd.sh"

cat > "$tmp/docker-compose.base.yml" <<'YAML'
services:
  llama-server:
    image: example/llama
  open-webui:
    image: example/webui
  dashboard:
    image: example/dashboard
  dashboard-api:
    image: example/dashboard-api
YAML

cat > "$tmp/docker-compose.nvidia.yml" <<'YAML'
services:
  llama-server:
    environment:
      GPU_BACKEND: nvidia
YAML

mkdir -p "$tmp/extensions/services/whisper"
cat > "$tmp/extensions/services/whisper/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: whisper
  category: optional
  gpu_backends: ["nvidia", "none"]
  compose_file: compose.yaml
YAML

cat > "$tmp/extensions/services/whisper/compose.yaml" <<'YAML'
services:
  whisper:
    image: ${WHISPER_IMAGE:-ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu}
YAML

cat > "$tmp/extensions/services/whisper/compose.nvidia.yaml" <<'YAML'
services:
  whisper:
    image: ${WHISPER_IMAGE:-ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cuda}
YAML

default_flags="$(bash "$RESOLVER" --script-dir "$tmp" --tier 1 --gpu-backend nvidia --gpu-count 1)"
case "$default_flags" in
    *"extensions/services/whisper/compose.yaml"* ) ;;
    * ) fail "default resolver dropped Whisper base compose: $default_flags" ;;
esac
case "$default_flags" in
    *"extensions/services/whisper/compose.nvidia.yaml"* ) ;;
    * ) fail "default resolver did not include Whisper NVIDIA overlay: $default_flags" ;;
esac

fallback_flags="$(bash "$RESOLVER" --script-dir "$tmp" --tier 1 --gpu-backend nvidia --gpu-count 1 --skip-gpu-overlays whisper)"
case "$fallback_flags" in
    *"extensions/services/whisper/compose.yaml"* ) ;;
    * ) fail "fallback resolver dropped Whisper base compose: $fallback_flags" ;;
esac
case "$fallback_flags" in
    *"extensions/services/whisper/compose.nvidia.yaml"* ) fail "fallback resolver re-added Whisper NVIDIA overlay: $fallback_flags" ;;
esac
case "$fallback_flags" in
    *"docker-compose.nvidia.yml"* ) ;;
    * ) fail "fallback resolver dropped base NVIDIA LLM overlay: $fallback_flags" ;;
esac

pass "Windows Whisper CPU fallback can skip only the Whisper GPU overlay"
