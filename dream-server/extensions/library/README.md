# DreamServer Extensions Library

**32 service extensions being tested for DreamServer. 17 are already in production — these are next.**

Each extension is a self-contained directory with a `manifest.yaml` (service metadata), `compose.yaml` (Docker Compose fragment), and optional Dockerfiles, workflows, and documentation. Drop any of these into your DreamServer's `extensions/services/` directory and run `dream enable <service-id>`.

---

## Services by Category

### LLM Inference & Chat

| Service | Description | GPU |
|---------|------------|-----|
| [`ollama/`](services/ollama/) | Ollama — pull-and-run LLM server | AMD, NVIDIA |
| [`localai/`](services/localai/) | LocalAI — OpenAI-compatible API for local models | AMD, NVIDIA |
| [`text-generation-webui/`](services/text-generation-webui/) | Oobabooga — full-featured LLM UI with LoRA, GPTQ, quantization | AMD, NVIDIA |
| [`jan/`](services/jan/) | Jan — local ChatGPT alternative with model management | AMD, NVIDIA |
| [`librechat/`](services/librechat/) | LibreChat — multi-provider chat UI (local + cloud) | AMD, NVIDIA, Apple |

### Voice & Audio

| Service | Description | GPU |
|---------|------------|-----|
| [`bark/`](services/bark/) | Bark TTS — expressive speech with laughter, emotion, 13 languages | NVIDIA |
| [`xtts/`](services/xtts/) | Coqui XTTS — voice cloning and multilingual TTS | AMD, NVIDIA |
| [`piper-audio/`](services/piper-audio/) | Piper — fast, lightweight TTS for edge devices | AMD, NVIDIA, Apple |
| [`rvc/`](services/rvc/) | RVC — real-time voice conversion/cloning | AMD, NVIDIA |
| [`audiocraft/`](services/audiocraft/) | Meta AudioCraft — text-to-music and sound effects | NVIDIA |

### Image Generation

| Service | Description | GPU |
|---------|------------|-----|
| [`comfyui/`](services/comfyui/) | ComfyUI — node-based Stable Diffusion workflows | AMD, NVIDIA |
| [`fooocus/`](services/fooocus/) | Fooocus — simplified Stable Diffusion (Midjourney-like UX) | NVIDIA |
| [`invokeai/`](services/invokeai/) | InvokeAI — professional Stable Diffusion with canvas | AMD, NVIDIA |
| [`forge/`](services/forge/) | Forge / A1111 — Stable Diffusion WebUI with optimizations | NVIDIA |

### AI Development & Agents

| Service | Description | GPU |
|---------|------------|-----|
| [`aider/`](services/aider/) | Aider — AI pair programming in your terminal | AMD, NVIDIA, Apple |
| [`continue/`](services/continue/) | Continue — AI coding assistant (VS Code / JetBrains) | AMD, NVIDIA, Apple |
| [`crewai/`](services/crewai/) | CrewAI — multi-agent orchestration framework | CPU |
| [`open-interpreter/`](services/open-interpreter/) | Open Interpreter — natural language → system commands | CPU |
| [`jupyter/`](services/jupyter/) | Jupyter — notebooks with local LLM kernel | AMD, NVIDIA |

### Vector Databases

| Service | Description | GPU |
|---------|------------|-----|
| [`chromadb/`](services/chromadb/) | ChromaDB — lightweight embedding database | AMD, NVIDIA, Apple |
| [`milvus/`](services/milvus/) | Milvus — production-grade vector database | CPU |
| [`weaviate/`](services/weaviate/) | Weaviate — vector search with hybrid ranking | CPU |

### Workflow Automation

| Service | Description | GPU |
|---------|------------|-----|
| [`flowise/`](services/flowise/) | Flowise — drag-and-drop LLM chain builder | AMD, NVIDIA, Apple |
| [`langflow/`](services/langflow/) | Langflow — visual LangChain builder | AMD, NVIDIA, Apple |
| [`dify/`](services/dify/) | Dify — LLMOps platform with RAG and agents | AMD, NVIDIA |

### Self-Hosted Apps

| Service | Description | GPU |
|---------|------------|-----|
| [`immich/`](services/immich/) | Immich — Google Photos alternative with AI face/object detection | AMD, NVIDIA |
| [`paperless-ngx/`](services/paperless-ngx/) | Paperless-ngx — document management with OCR | CPU |
| [`frigate/`](services/frigate/) | Frigate — NVR with real-time AI object detection | NVIDIA |
| [`gitea/`](services/gitea/) | Gitea — lightweight self-hosted Git | CPU |
| [`baserow/`](services/baserow/) | Baserow — open-source Airtable alternative | CPU |
| [`sillytavern/`](services/sillytavern/) | SillyTavern — advanced roleplay/chat frontend | AMD, NVIDIA, Apple |

### Data & ML

| Service | Description | GPU |
|---------|------------|-----|
| [`label-studio/`](services/label-studio/) | Label Studio — data labeling for ML training | CPU |
| [`anythingllm/`](services/anythingllm/) | AnythingLLM — all-in-one RAG + chat + agents | AMD, NVIDIA |

## Platform Compatibility

Quick reference for hardware requirements. Data sourced from each service's `manifest.yaml`.

| Service | NVIDIA | AMD | Apple Silicon | CPU Only | Min VRAM |
|---------|:------:|:---:|:-------------:|:--------:|:--------:|
| aider | ✓ | ✓ | ✓ | — | — |
| anythingllm | ✓ | ✓ | — | — | 2 GB |
| audiocraft | ✓ | — | — | — | 6 GB |
| bark | ✓ | — | — | — | 4 GB |
| baserow | — | — | — | ✓ | — |
| chromadb | ✓ | ✓ | ✓ | — | — |
| continue | ✓ | ✓ | ✓ | — | 4 GB |
| crewai | — | — | — | ✓ | — |
| dify | ✓ | ✓ | — | — | 4 GB |
| flowise | ✓ | ✓ | ✓ | — | — |
| fooocus | ✓ | — | — | — | 8 GB |
| forge | ✓ | — | — | — | 8 GB |
| frigate | ✓ | — | — | — | 1 GB |
| gitea | — | — | — | ✓ | — |
| immich | ✓ | ✓ | — | — | 2 GB |
| invokeai | ✓ | ✓ | — | — | 8 GB |
| jan | ✓ | ✓ | — | — | — |
| jupyter | ✓ | ✓ | — | — | 4 GB |
| label-studio | — | — | — | ✓ | — |
| langflow | ✓ | ✓ | ✓ | — | — |
| librechat | ✓ | ✓ | ✓ | — | — |
| localai | ✓ | ✓ | — | — | 4 GB |
| milvus | — | — | — | ✓ | — |
| ollama | ✓ | ✓ | — | — | 8 GB |
| open-interpreter | — | — | — | ✓ | — |
| paperless-ngx | — | — | — | ✓ | — |
| piper-audio | ✓ | ✓ | ✓ | — | — |
| rvc | ✓ | ✓ | — | — | 6 GB |
| sillytavern | ✓ | ✓ | ✓ | — | — |
| text-generation-webui | ✓ | ✓ | — | — | 4 GB |
| weaviate | — | — | — | ✓ | — |
| xtts | ✓ | ✓ | — | — | — |

> **Note:** "Min VRAM" shows the minimum GPU memory needed for the service's base feature. Some advanced features may require more. Services marked "CPU Only" have no GPU acceleration. Apple Silicon support means the service can use Metal/MPS for acceleration.

---

## Workflows

Pre-built automation workflows for n8n, ComfyUI, Flowise, Langflow, and more:

```
workflows/
├── bark/          — Voice synthesis pipelines
├── comfyui/       — LLM-to-image generation
├── flowise/       — Chatflow API templates
├── langflow/      — Flow API templates
├── n8n/           — Webhooks, scheduling, form processing, DB sync
├── piper/         — TTS conversion
├── rvc/           — Voice conversion
├── sillytavern/   — Status monitoring
├── text-generation-webui/  — Chat completion, model listing
└── whisper/       — Speech-to-text conversion
```

## Templates

Everything you need to build your own extension:

| Template | Purpose |
|----------|---------|
| `service-template.yaml` | Complete manifest with every field documented |
| `compose-template.yaml` | Compose fragment with best practices |
| `compose-gpu-only.yaml` | GPU-only service pattern (no CPU fallback) |
| `compose-gpu-swap.yaml` | CPU base + GPU overlay pattern |
| `dashboard-plugin-template.js` | Dashboard UI plugin scaffold |

## Schema

`schema/service-manifest.v1.json` — JSON Schema for validating manifest files.

```bash
# Validate any manifest:
python3 -c "
import json, yaml
from jsonschema import validate
schema = json.load(open('schema/service-manifest.v1.json'))
manifest = yaml.safe_load(open('services/bark/manifest.yaml'))
validate(manifest, schema)
print('Valid!')
"
```

---

## How to Use

**Add a service to your DreamServer:**
```bash
# Copy the extension into your DreamServer
cp -r dream-server/extensions/library/services/bark dream-server/extensions/services/bark

# Enable and start it
dream enable bark
dream start bark
```

**Build your own extension:**
```bash
# Copy the template
cp -r dream-server/extensions/library/templates/ my-service/
mv my-service/service-template.yaml my-service/manifest.yaml
mv my-service/compose-template.yaml my-service/compose.yaml

# Edit, then validate
python3 -c "import yaml; yaml.safe_load(open('my-service/manifest.yaml'))"
```

---

## Status

These extensions are actively tested on DreamServer development builds. Some are battle-tested (Ollama, ChromaDB, Bark), others are newer. All follow the v1 manifest schema and integrate with the DreamServer service registry, dashboard, and CLI.

**17 services have already graduated to production** — these 32 are being prepared for the next wave.
