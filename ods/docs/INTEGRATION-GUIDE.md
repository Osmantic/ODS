# ODS Integration Guide

You've got ODS running. This guide connects applications to the stable,
OpenAI-compatible API contract that ODS exposes through LiteLLM.

## Before you connect

LiteLLM is the recommended integration boundary because its URL and public
model alias stay the same when ODS moves between local, cloud, and hybrid
modes. Ensure the recommended service is enabled:

```bash
ods enable litellm
```

Use these values in the examples below:

- API base: `http://localhost:4000/v1`
- API key: the generated `LITELLM_KEY` from your ODS `.env`
- model: `default`

Treat `LITELLM_KEY` as a secret. Replace `YOUR_LITELLM_KEY` in configuration
files, or inject it through the application's secret mechanism. Check the live
catalog before relying on an additional mode-specific alias:

```bash
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer YOUR_LITELLM_KEY"
```

Direct llama-server ports are platform-dependent (`OLLAMA_PORT` defaults to
11434 on Linux/WSL and 8080 for the native macOS runtime). Use them only when
you intentionally want to bypass ODS mode routing and LiteLLM authentication.

---

## 1. OpenAI SDK compatibility

### Python

```bash
pip install openai
```

```python
import os

from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key=os.environ["LITELLM_KEY"],
)

response = client.chat.completions.create(
    model="default",
    messages=[{"role": "user", "content": "Hello!"}],
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
  baseURL: 'http://localhost:4000/v1',
  apiKey: process.env.LITELLM_KEY,
});

const response = await openai.chat.completions.create({
  model: 'default',
  messages: [{ role: 'user', content: 'Hello!' }],
});

console.log(response.choices[0].message.content);
```

### curl

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_LITELLM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 2. LangChain integration

```bash
pip install langchain-openai
```

```python
import os

from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key=os.environ["LITELLM_KEY"],
    model="default",
    temperature=0.7,
)

response = llm.invoke("Explain quantum computing in one sentence.")
print(response.content)
```

### With bundled embeddings and Qdrant

The embeddings extension exposes Hugging Face TEI at port 8090. Its documented
public request shape is `POST /embed`, separate from the LiteLLM chat API:

```python
import os

import requests
from qdrant_client import QdrantClient

vectors = requests.post(
    "http://localhost:8090/embed",
    json={"inputs": ["ODS runs locally", "Private RAG pipeline"]},
    timeout=30,
).json()

qdrant = QdrantClient(
    url="http://localhost:6333",
    api_key=os.environ.get("QDRANT_API_KEY"),
)
```

Enable both extensions before using this path:

```bash
ods enable embeddings
ods enable qdrant
```

---

## 3. Continue setup

Continue's current configuration format is `~/.continue/config.yaml`; the old
`config.json` format is deprecated. Add an OpenAI-compatible model:

```yaml
name: ODS
version: 1.0.0
schema: v1

models:
  - name: ODS
    provider: openai
    model: default
    apiBase: http://localhost:4000/v1
    apiKey: YOUR_LITELLM_KEY
    roles:
      - chat
      - edit
      - apply
```

See Continue's [OpenAI-compatible provider guide](https://docs.continue.dev/customize/model-providers/top-level/openai)
and [config.yaml reference](https://docs.continue.dev/reference) for additional
roles and secret handling.

---

## 4. Cursor compatibility

Cursor's OpenAI base-URL override is not a supported direct-local integration
for ODS. Cursor currently routes those requests through its backend and does
not accept a `localhost` or LAN-only endpoint. Using a public HTTPS tunnel would
also change the local-only security boundary that ODS provides.

Use Continue or an OpenAI SDK client for a direct local connection. If you
choose to expose ODS through a reviewed HTTPS gateway, configure Cursor's
**Settings -> Models -> OpenAI API Key -> Override OpenAI Base URL** with the
gateway URL and understand that the override affects Cursor's OpenAI-compatible
model routing. Cursor documents the current localhost limitation in its
[official community response](https://forum.cursor.com/t/add-an-option-to-add-local-model-in-the-same-machine-or-lan-with-just-the-ip-and-http/148311/3).

---

## 5. n8n workflow examples

ODS includes n8n for workflow automation at `http://localhost:5678`.

1. Open n8n and log in with `N8N_USER` / `N8N_PASS` from `.env`.
2. Create or import a workflow.
3. In an HTTP Request node, use
   `http://litellm:4000/v1/chat/completions` (the Docker-internal URL).
4. Add `Authorization: Bearer YOUR_LITELLM_KEY` and
   `Content-Type: application/json` headers.
5. Send `model: default` in the JSON body.

| Workflow | Description |
|----------|-------------|
| Chat endpoint | HTTP webhook -> LiteLLM -> response |
| Document Q&A | File upload -> embeddings -> Qdrant -> LiteLLM |
| Voice transcription | Audio -> Whisper STT -> text |
| TTS API | Text -> Kokoro TTS -> audio |
| Voice-to-voice | STT -> LiteLLM -> TTS pipeline |

---

## 6. REST API reference

All paths below are relative to `http://localhost:4000` and require a Bearer
token containing `LITELLM_KEY`.

| Endpoint | Description |
|----------|-------------|
| `POST /v1/chat/completions` | Chat completion (OpenAI compatible) |
| `POST /v1/completions` | Text completion |
| `GET /v1/models` | List configured model aliases |
| `GET /health/readiness` | LiteLLM readiness probe |

### Streaming

```python
import os

from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key=os.environ["LITELLM_KEY"],
)

stream = client.chat.completions.create(
    model="default",
    messages=[{"role": "user", "content": "Write a poem"}],
    stream=True,
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## 7. Environment variables

Key variables in `.env` (see [.env.example](../.env.example) for the complete
schema-backed example):

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_PORT` | 4000 | Stable external LLM gateway port |
| `LITELLM_KEY` | generated | Required LiteLLM master key |
| `ODS_MODE` | local | `local`, `cloud`, or `hybrid` routing |
| `OLLAMA_PORT` | platform-dependent | Direct llama-server port; bypasses the gateway |
| `EMBEDDINGS_PORT` | 8090 | Bundled TEI embeddings API |
| `QDRANT_PORT` | 6333 | Bundled vector database API |
| `LLM_MODEL` | tier-dependent | Configured local backend model |
| `CTX_SIZE` | 16384 | Context-window size unless the tier overrides it |

---

## 8. Authentication and exposure

LiteLLM requires `LITELLM_KEY`; there is no supported unauthenticated custom
application path through the gateway. Send it as an OpenAI-style Bearer token:

```text
Authorization: Bearer YOUR_LITELLM_KEY
```

ODS binds service ports to `127.0.0.1` by default. Do not change
`BIND_ADDRESS` or expose port 4000 to a LAN/public network without adding a
reviewed TLS and access-control boundary. Open WebUI has its own user-management
setting (`WEBUI_AUTH`) and is separate from LiteLLM API authentication.

---

## Common issues

### 401 Unauthorized

Confirm the request contains the current `LITELLM_KEY` value from the active
ODS `.env`. Restarted clients can retain an older key.

### Model not found

Query the authenticated catalog and use an ID it returns. `default` is the
stable cross-mode alias shipped by ODS:

```bash
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer YOUR_LITELLM_KEY"
```

### Connection refused

Check the gateway and its logs:

```bash
ods status
docker compose logs litellm --tail 50
```

If LiteLLM is disabled, run `ods enable litellm` and then `ods restart`.

### Slow first response

The first local request after startup may materialize model and KV-cache state.
Follow backend logs with `ods logs llm` while it warms up.

---

*Built by The Collective*
