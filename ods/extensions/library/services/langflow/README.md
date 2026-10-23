# Langflow

Visual LLM workflow builder with drag-and-drop interface. Create complex AI workflows, RAG pipelines, and AI agents using LangChain components with real-time testing.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
ods enable langflow
ods disable langflow
```

Your data is preserved when disabling. To re-enable later: `ods enable langflow`

## Access

- **URL:** `http://localhost:7802`

## Authentication

Langflow flows can execute arbitrary Python, so ODS disables Langflow's
`AUTO_LOGIN` default and requires a superuser login.

| Variable | Required | Notes |
|---|---|---|
| `LANGFLOW_SUPERUSER` | no | Username, default `admin` |
| `LANGFLOW_SUPERUSER_PASSWORD` | yes | The dashboard install dialog asks for it; **Generate** fills a random value |
| `LANGFLOW_SECRET_KEY` | yes | 64 hex characters, same dialog; keep it stable or stored credentials become unreadable |

The dashboard refuses to install or enable Langflow until both are saved, so
nothing reaches Compose without them. `ods enable` does not generate settings
for any extension: when using the CLI, set both in `.env` first (for example
`openssl rand -hex 32` for each). Compose refuses to start Langflow without them.

**Upgrading an existing install:** an installed copy keeps its previous
definition (with `AUTO_LOGIN`) until you choose **Update** for Langflow in the
dashboard. Before updating, save both settings (or add them to `.env`);
otherwise the updated definition cannot start and the dashboard restores the
previous one.

## First-Time Setup

1. Enable the service: `ods enable langflow`
2. Open `http://localhost:7802` and sign in as the superuser
3. Create a new flow from the template gallery or start from scratch
4. Drag LLM, retriever, and tool nodes onto the canvas

### API Usage

```bash
# Run a flow
curl -X POST http://localhost:7802/api/v1/run/<flow_id> \
  -H "Content-Type: application/json" \
  -d '{"input_value": "Hello, what can you help me with?"}'
```
