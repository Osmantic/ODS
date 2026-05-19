# Flowise Workflows

This directory contains n8n workflow templates for integrating Flowise with Dream Server.

## Available Workflows

### Chatflow API (`chatflow-api.json`)

Expose Flowise chatflows via webhook with optional API key authentication.

**Flow:**
1. **Webhook**: Receives POST requests at `/flowise-chat`
2. **Validation**: Validates API key and extracts chatflowId
3. **Flowise API**: Calls Flowise prediction endpoint
4. **Response**: Returns generated text, source docs, and tool usage

**Environment Variables:**

| Variable | Description | Default |
|----------|-------------|---------|
| `FLOWISE_HOST` | Flowise hostname | `flowise` |
| `FLOWISE_PORT` | Flowise port | `3000` |
| `FLOWISE_API_KEY` | Optional webhook API key | (none) |
| `FLOWISE_DEFAULT_CHATFLOW_ID` | Default chatflow if not provided | (none) |

**Usage:**

```bash
# With specific chatflow
curl -X POST http://localhost:5678/webhook/flowise-chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "chatflowId": "your-chatflow-id",
    "question": "What is the weather today?"
  }'

# With default chatflow (set FLOWISE_DEFAULT_CHATFLOW_ID)
curl -X POST http://localhost:5678/webhook/flowise-chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Explain quantum computing"
  }'
```

**Response:**

```json
{
  "text": "Quantum computing uses quantum bits...",
  "chatId": "uuid",
  "sourceDocuments": [...],
  "usedTools": [...]
}
```

## Setup

1. Import workflow into n8n
2. Configure environment variables
3. Create a chatflow in Flowise UI
4. Copy the chatflow ID from the URL
5. Test via webhook

## Integration with Dream Server

Connect Flowise to Dream Server's LLM:
1. In Flowise, add an "Ollama" or "ChatLocalAI" node
2. Set Base URL: `http://llama-server:8000/v1`
3. Use local models already downloaded

## Resources

- [Flowise API Docs](https://docs.flowiseai.com/api-reference)
- [n8n Webhook Node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/)
