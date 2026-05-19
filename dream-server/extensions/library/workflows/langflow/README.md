# Langflow Workflows

This directory contains n8n workflow templates for integrating Langflow with Dream Server.

## Available Workflows

### Flow API (`flow-api.json`)

Expose Langflow flows via webhook with optional API key authentication.

**Flow:**
1. **Webhook**: Receives POST requests at `/langflow-run`
2. **Validation**: Validates API key and extracts flowId
3. **Langflow API**: Calls Langflow run endpoint
4. **Response**: Returns generated text and session info

**Environment Variables:**

| Variable | Description | Default |
|----------|-------------|---------|
| `LANGFLOW_HOST` | Langflow hostname | `langflow` |
| `LANGFLOW_PORT` | Langflow port | `7860` |
| `LANGFLOW_API_KEY` | Optional webhook API key | (none) |
| `LANGFLOW_DEFAULT_FLOW_ID` | Default flow if not provided | (none) |

**Usage:**

```bash
# With specific flow
curl -X POST http://localhost:5678/webhook/langflow-run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "flowId": "your-flow-id",
    "input": "Generate a marketing email"
  }'

# With default flow (set LANGFLOW_DEFAULT_FLOW_ID)
curl -X POST http://localhost:5678/webhook/langflow-run \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Summarize this article"
  }'
```

**Response:**

```json
{
  "text": "Here's your marketing email...",
  "session_id": "uuid",
  "outputs": [
    {"component": "ChatInput-abc123", "type": "chat"}
  ]
}
```

## Setup

1. Import workflow into n8n
2. Configure environment variables
3. Create a flow in Langflow UI
4. Copy the flow ID from the URL
5. Test via webhook

## Integration with Dream Server

Connect Langflow to Dream Server's LLM:
1. In Langflow, add an "Ollama" or "OpenAI" component
2. Set Base URL: `http://llama-server:8000/v1`
3. Use local models already downloaded

## Langflow vs Flowise

| Feature | Langflow | Flowise |
|---------|----------|---------|
| Framework | LangChain | LangChain / custom |
| Components | LangChain native | Pre-built nodes |
| Custom Code | Python components | JavaScript functions |
| API | Simpler | More flexible |

Choose Langflow for LangChain-native workflows. Choose Flowise for more pre-built integrations.

## Resources

- [Langflow API Docs](https://docs.langflow.org/api)
- [n8n Webhook Node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/)
