#!/usr/bin/env bash
# Post-copy setup for cafe-llama library extension.
set -euo pipefail
echo "cafe-llama: optional advanced runtime"
echo "  1. Ensure a cafe-llama.cpp llama-server binary is available"
echo "     (build image with CAFE_LLAMA_RELEASE_URL or set CAFE_LLAMA_SERVER)."
echo "  2. Set CAFE_LLAMA_MODEL to a GGUF/safetensors path under the models volume."
echo "  3. Enable with: ods enable cafe-llama"
echo "  4. Core LLM_BACKEND=llama-server remains unchanged."
exit 0
