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
| `LANGFLOW_SECRET_KEY` | yes | At least 32 characters; **Generate** fills 64 hex characters. Existing Langflow-generated URL-safe keys are accepted. Keep the value stable or stored credentials become unreadable |

The dashboard refuses to install or enable Langflow until both are saved, so
nothing reaches Compose without them. `ods enable` does not generate settings
for any extension: when using the CLI, set both in `.env` first (for example
`openssl rand -hex 32` for each). Compose refuses to start Langflow without them.

**Upgrading an existing install:** an installed copy keeps its previous
definition (with `AUTO_LOGIN`) until you choose **Update** for Langflow in the
dashboard. Back up the Langflow data before updating. Reuse the current
`LANGFLOW_SECRET_KEY` for an existing database; if Langflow generated it, retain
the value from its configuration directory's `secret_key` file. Generating a new
key does not migrate stored credentials and makes them unreadable.

`LANGFLOW_SUPERUSER_PASSWORD` initializes a missing account; it does not reset an
existing superuser's password. Sign in with that account's existing password.
If the chosen username already belongs to a regular user, choose an unused
`LANGFLOW_SUPERUSER` name before updating. Disabling automatic login does not
remove every existing account: change any known/default passwords on retained
accounts before exposing the service.

Save both required settings (or add them to `.env`) before choosing **Update**.
Without them, the updated definition cannot start and the dashboard restores
the previous definition. Restoring the definition does not undo database or
encryption-key changes.

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
