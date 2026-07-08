# MEMORY.md - Todd's Long-Term Memory

## ODS — System Knowledge

### Hardware
- **CPU:** AMD Ryzen AI MAX+ 395 (Strix Halo)
- **GPU:** Radeon 8060S (RDNA 3.5, gfx1151)
- **Memory:** 128GB unified (96GB VRAM / 32GB CPU, configured in BIOS)
- **Machine:** GMKtec NucBox EVO-X2

### Inference Stack
- **Model:** qwen3-coder-next (80B MoE, 3B active params, ~52GB)
- **Format:** GGUF (Q4_K_M quantization, from unsloth/Qwen3-Coder-Next-GGUF)
- **Backend:** Lemonade Server via ROCm (NOT Ollama, NOT Vulkan)
- **Container:** ghcr.io/lemonade-sdk/lemonade-server:v10.0.0
- **Context:** 32,768 tokens
- **Key flags:** `-fa on --no-mmap -ngl 999 --jinja`
- **Env:** `ROCBLAS_USE_HIPBLASLT=0`, `HSA_OVERRIDE_GFX_VERSION=11.5.1`

### Services & Ports
| Service | Port | Profile | Notes |
|---------|------|---------|-------|
| Open WebUI | 3000 | default | Chat interface, connects via OpenAI-compatible API |
| Dashboard | 3001 | default | React (Vite) system dashboard |
| Dashboard API | 3002 | default | FastAPI backend for dashboard |
| SearXNG | 8888 | default | Self-hosted metasearch (internal: searxng:8080) |
| LiteLLM | 4000 | monitoring | Proxy/router |
| n8n | 5678 | workflows | Workflow automation |
| Qdrant | 6333 | rag | Vector database for RAG |
| OpenClaw | 7860 | openclaw | That's me! Agent interface |
| Embeddings | 8090 | rag | Text embeddings service |
| Kokoro TTS | 8880 | voice | Text-to-speech |
| Whisper STT | 9000 | voice | Speech-to-text |
| llama-server | 11434 | default | LLM inference (OpenAI-compatible) |
| ComfyUI | 8188 | comfyui | Image generation |

### How I Can Help Users
- **Web search:** I have a native `web_search` tool backed by SearXNG — use it for current info, docs, or anything beyond training data
- **Chat:** Open WebUI at port 3000 — main conversational interface
- **Workflows:** n8n at port 5678 — automate tasks, connect services, build pipelines
- **Voice:** Whisper (STT) + Kokoro (TTS) — voice input/output for the chat
- **RAG:** Qdrant + Embeddings — upload documents, chat with your data
- **Dashboard:** Port 3001 — monitor system status, GPU usage, model info
- **Image gen:** ComfyUI at port 8188 — local image generation
- **Automation ideas:** RSS feeds, scheduled summaries, webhook integrations via n8n

### Key Technical Notes
- Everything runs locally — zero cloud dependency, total privacy
- Zero cost per token — all inference on local hardware
- Web search via SearXNG — self-hosted, no API keys, aggregates DuckDuckGo/Google/Brave/Wikipedia/GitHub/StackOverflow
- ROCm 7.2 is required (Vulkan crashes on qwen3-coder-next architecture)
- Services behind profiles must be enabled: `COMPOSE_PROFILES=voice,rag,workflows,openclaw`
- Docker compose files: `docker-compose.base.yml` + `docker-compose.amd.yml`
