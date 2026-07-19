# ODS Integration Guide

You've got ODS running. Now what? This guide shows how to connect your apps.

---

## 1. OpenAI SDK Compatibility

ODS exposes an OpenAI-compatible API. Just point your SDK at localhost.

### Python

```bash
pip install openai
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",  # ODS llama-server
    api_key="not-needed"  # Local, no auth required
)

response = client.chat.completions.create(
    model="qwen2.5-32b-instruct",  # Your running model
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
print(response.choices[0].message.content)
```

### Node.js / TypeScript

```bash
npm install openai
```

```javascript
import OpenAI from 'openai';

const openai = new OpenAI({
  baseURL: 'http://localhost:8080/v1',
  apiKey: 'not-needed',
});

const response = await openai.chat.completions.create({
  model: 'qwen2.5-32b-instruct',
  messages: [{ role: 'user', content: 'Hello!' }],
});

console.log(response.choices[0].message.content);
```

### curl

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-32b-instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 2. LangChain Integration

```bash
pip install langchain langchain-openai
```

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8080/v1",
    api_key="not-needed",
    model="qwen2.5-32b-instruct",
    temperature=0.7,
)

response = llm.invoke("Explain quantum computing in one sentence.")
print(response.content)
```

### With RAG (Qdrant + Embeddings)

```python
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import Qdrant

embeddings = OpenAIEmbeddings(
    base_url="http://localhost:8090/v1",  # Embeddings service
    api_key="not-needed",
)

# Connect to ODS's Qdrant
qdrant = Qdrant.from_existing_collection(
    embeddings=embeddings,
    collection_name="documents",
    url="http://localhost:6333",
)

# Query documents
results = qdrant.similarity_search("What is the policy on refunds?")
```

---

## 3. Continue.dev Setup

Continue is an open-source AI code assistant that works in VS Code.

1. Install Continue extension in VS Code
2. Edit `~/.continue/config.json`:

```json
{
  "models": [
    {
      "title": "ODS",
      "provider": "openai",
      "model": "qwen2.5-32b-instruct",
      "apiBase": "http://localhost:8080/v1",
      "apiKey": "not-needed"
    }
  ]
}
```

3. Restart VS Code, select "ODS" in Continue panel

---

## 4. Cursor IDE Integration

Cursor supports custom API endpoints.

1. Open Cursor Settings → Models
2. Add custom model:
   - **API Base:** `http://localhost:8080/v1`
   - **API Key:** `not-needed`
   - **Model:** `qwen2.5-32b-instruct`

---

## 5. n8n Workflow Examples

ODS includes n8n for workflow automation. Access at `http://localhost:5678`.

### Creating Workflows

1. Open n8n at http://localhost:5678
2. Log in with the credentials from your `.env` (`N8N_USER` / `N8N_PASS`)
3. Create a new workflow or import from the n8n template library
4. Use the "HTTP Request" node pointed at `http://llama-server:8080/v1/chat/completions` (Docker-internal URL)

### Example Workflow Ideas

| Workflow | Description |
|----------|-------------|
| Chat Endpoint | HTTP webhook → LLM → response |
| Document Q&A | File upload → embeddings → Qdrant → LLM |
| Voice Transcription | Audio → Whisper STT → text |
| TTS API | Text → Kokoro TTS → audio |
| Voice-to-Voice | STT → LLM → TTS pipeline |

---

## 6. REST API Reference

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /v1/chat/completions` | Chat (OpenAI compatible) |
| `POST /v1/completions` | Text completion |
| `GET /v1/models` | List available models |
| `GET /health` | Health check |

### Streaming

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="x")

stream = client.chat.completions.create(
    model="qwen2.5-32b-instruct",
    messages=[{"role": "user", "content": "Write a poem"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## 7. Environment Variables

Key variables in `.env` (see [.env.example](../.env.example) for the full list):

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_PORT` | 11434 | llama-server external port (maps to internal 8080) |
| `WEBUI_PORT` | 3000 | Open WebUI port |
| `N8N_PORT` | 5678 | n8n workflows port |
| `LLM_MODEL` | *(tier-dependent)* | Model name for OpenClaw/dashboard |
| `CTX_SIZE` | 16384 | Context window size (tokens) |
| `GGUF_FILE` | *(tier-dependent)* | GGUF model filename in data/models/ |

---

## 8. Authentication Options

### No Auth (Default)

Local-only, no auth required. Good for development.

### API Key Auth

Set in `.env`:
```
LLM_API_KEY=your-secret-key
```

Then include in requests:
```python
client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="your-secret-key"
)
```

### Open WebUI Auth

WebUI has built-in user management:
1. First user becomes admin
2. Configure auth in WebUI settings
3. Set `WEBUI_AUTH=true` in `.env`

---

## Common Issues

### "Model not found"

Check running model name:
```bash
curl http://localhost:8080/v1/models
```

Use the exact model name in your requests.

### Connection refused

Ensure services are running:
```bash
docker compose ps
```

Check llama-server is ready:
```bash
docker compose logs llama-server | tail -20
```

### Slow first response

First request after start triggers model warm-up. Wait 30-60 seconds.

---

*Built by The Collective*
