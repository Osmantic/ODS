# Jan

A ChatGPT alternative that runs 100% offline on your computer. Multi-engine support (llama.cpp, TensorRT-LLM) with built-in model management — local-first, privacy-focused.

## Requirements

- **GPU:** NVIDIA or AMD
- **Dependencies:** None

## Enable / Disable

```bash
dream enable jan
dream disable jan
```

Your data is preserved when disabling. To re-enable later: `dream enable jan`

## Access

- **URL:** `http://localhost:1337`

## First-Time Setup

1. Enable the service: `dream enable jan`
2. Open `http://localhost:1337`
3. Download models through the UI or place them in `./data/jan/models/`
