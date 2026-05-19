# Text Generation WebUI

The most feature-complete local LLM inference interface (Oobabooga). Supports GPTQ, GGUF, AWQ, EXL2, and HF transformer formats with chat UI, API server, extensions, LoRA loading, and fine-grained generation controls.

## Requirements

- **GPU:** NVIDIA or AMD (min 4 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable text-generation-webui
dream disable text-generation-webui
```

Your data is preserved when disabling. To re-enable later: `dream enable text-generation-webui`

## Access

- **URL:** `http://localhost:7862`
- **API:** `http://localhost:5001` (OpenAI-compatible)

## First-Time Setup

1. Enable the service: `dream enable text-generation-webui`
2. Open `http://localhost:7862`
3. Go to the Model tab to download or load a model
4. Place GGUF or other model files in `./data/text-generation-webui/models/` and refresh the model list

### API Usage

```bash
curl http://localhost:5001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "your-model", "messages": [{"role": "user", "content": "Hello!"}]}'
```
