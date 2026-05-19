# ComfyUI Workflows

This directory contains n8n workflow templates for integrating ComfyUI with other services.

## Available Workflows

### 1. LLM to Image Generation (`llm-to-image.json`)

This workflow enhances prompts with the LLM, then uses a **predefined ComfyUI workflow** with the enhanced prompt:

1. **Webhook**: Receives prompts with optional API key authentication
2. **Input Validation**: Validates prompt length, channel format
3. **LLM Enhancement**: Sends to LLM to generate an enhanced image generation prompt
4. **ComfyUI Execution**: Uses the predefined workflow with the enhanced prompt
5. **Output**: Returns enhanced prompt and status to Discord

#### Use Case
Best for consistent, high-quality image generation with prompt engineering.

#### Environment Variables

- `LLM_HOST` - LLM server host (default: localhost)
- `LLM_PORT` - LLM port (default: 8080)
- `COMFYUI_HOST` - ComfyUI host (default: localhost)
- `COMFYUI_PORT` - ComfyUI port (default: 8188)
- `COMFYUI_MODEL_NAME` - ComfyUI model name (default: flux1-dev.safetensors)
- `LLM_TO_IMAGE_API_KEY` - Optional API key for webhook authentication

#### Usage

```bash
curl -X POST http://localhost:5678/webhook/llm-to-image \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "prompt": "A beautiful landscape with mountains",
    "channel": "#art"
  }'
```

### 2. Dynamic Workflow Generation (`llm-image-gen.json`)

This workflow enables AI-powered image generation by having the LLM **generate the ComfyUI workflow JSON dynamically**:

1. **Webhook**: Receives image generation prompts via POST to `/comfyui-dynamic-workflow`
2. **LLM Processing**: Sends the prompt to the LLM (llama-server) to generate a ComfyUI workflow JSON
3. **ComfyUI Execution**: Submits the dynamically generated workflow to ComfyUI for image generation
4. **Output**: Returns status to Discord

#### Use Case
Best when you want the LLM to adapt the ComfyUI workflow structure based on the prompt complexity and requirements.

#### Environment Variables

- `LLM_HOST` - LLM server host (default: localhost)
- `LLM_API_PORT` - LLM API port (default: 8080)
- `COMFYUI_HOST` - ComfyUI host (default: localhost)
- `COMFYUI_PORT` - ComfyUI port (default: 8188)
- `COMFYUI_DYNAMIC_API_KEY` - Optional API key for webhook authentication

#### Usage

```bash
curl -X POST http://localhost:5678/webhook/comfyui-dynamic-workflow \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "prompt": "Create a photorealistic portrait of a cyberpunk samurai",
    "channel": "#art"
  }'
```

## Setup

1. Import workflows into n8n
2. Configure environment variables in your n8n instance
3. Ensure ComfyUI is running and accessible
4. Ensure LLM server (llama-server) is running and accessible
