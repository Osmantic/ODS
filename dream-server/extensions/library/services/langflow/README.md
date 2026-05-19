# Langflow

Visual LLM workflow builder with drag-and-drop interface. Create complex AI workflows, RAG pipelines, and AI agents using LangChain components with real-time testing.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
dream enable langflow
dream disable langflow
```

Your data is preserved when disabling. To re-enable later: `dream enable langflow`

## Access

- **URL:** `http://localhost:7802`

## First-Time Setup

1. Enable the service: `dream enable langflow`
2. Open `http://localhost:7802`
3. Create a new flow from the template gallery or start from scratch
4. Drag LLM, retriever, and tool nodes onto the canvas

### API Usage

```bash
# Run a flow
curl -X POST http://localhost:7802/api/v1/run/<flow_id> \
  -H "Content-Type: application/json" \
  -d '{"input_value": "Hello, what can you help me with?"}'
```
