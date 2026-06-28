#!/bin/bash
# ODS Offline Mode - Model Pre-download Check
# Verifies required models exist before starting services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ODS_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "ODS Offline Mode - Model Check"
echo "=========================================="
echo ""

MISSING=()

# Check LLM model (GGUF)
if ls data/models/*.gguf &>/dev/null; then
    MODEL_FILE=$(ls -1 data/models/*.gguf | sed -n '1p')
    echo -e "${GREEN}✓${NC} LLM model: $(basename "$MODEL_FILE")"
else
    echo -e "${RED}✗${NC} LLM model (GGUF) - MISSING"
    MISSING+=("gguf-model")
fi

# Check Whisper model
if [ -d "data/whisper/faster-whisper-base" ] || [ -d "data/whisper/models--Systran--faster-whisper-base" ]; then
    echo -e "${GREEN}✓${NC} Whisper base (STT)"
else
    echo -e "${RED}✗${NC} Whisper base - MISSING"
    MISSING+=("whisper-base")
fi

# Check Kokoro voice
if [ -f "data/kokoro/voices/af_heart.pt" ]; then
    echo -e "${GREEN}✓${NC} Kokoro voice af_heart (TTS)"
else
    echo -e "${RED}✗${NC} Kokoro voice af_heart - MISSING"
    MISSING+=("kokoro-af_heart")
fi

# Check embeddings model
if [ -d "data/embeddings/BAAI/bge-base-en-v1.5" ] || [ -d "data/embeddings/models--BAAI--bge-base-en-v1.5" ]; then
    echo -e "${GREEN}✓${NC} BGE base embeddings (RAG)"
else
    echo -e "${RED}✗${NC} BGE base embeddings - MISSING"
    MISSING+=("bge-base-en-v1.5")
fi

echo ""
echo "=========================================="

if [ ${#MISSING[@]} -eq 0 ]; then
    echo -e "${GREEN}All models present. Ready for offline mode!${NC}"
    exit 0
else
    echo -e "${RED}Missing models: ${#MISSING[@]}${NC}"
    echo ""
    echo "Download models with:"
    echo "  ./scripts/download-models.sh"
    echo ""
    echo "Or manually download:"
    for model in "${MISSING[@]}"; do
        echo "  - $model"
    done
    exit 1
fi
