# TOOLS.md - ODS Service Map

## Services

| Service | Docker Hostname | External Port |
|---------|-----------------|---------------|
| llama-server (LLM) | llama-server | 11434 |
| Open WebUI | open-webui | 3000 |
| SearXNG (search) | searxng | 8888 |
| Dashboard | dashboard | 3001 |
| Dashboard API | dashboard-api | 3002 |
| Whisper STT | whisper | 9000 (voice profile) |
| Kokoro TTS | tts | 8880 (voice profile) |
| n8n Workflows | n8n | 5678 (workflows profile) |
| Qdrant (RAG) | qdrant | 6333 (rag profile) |
| Embeddings | embeddings | 8090 (rag profile) |
| OpenClaw | openclaw | 7860 (openclaw profile) |
| ComfyUI | comfyui | 8188 (comfyui profile) |

## Network

All services share `ods-network`. Use Docker hostnames for inter-service calls.

Compose files: `docker-compose.base.yml` + GPU overlay (amd/nvidia/apple)

## Web Search

You have `web_search` (hits SearXNG) and `web_fetch` (loads page content). No API keys needed.
