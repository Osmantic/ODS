# n8n Workflow Templates

This directory contains import-ready n8n workflow JSON files for ODS.

## Setup

1. Start n8n service (via `install.sh --n8n` or manually)
2. Access n8n at `http://localhost:5678` (or configured port)
3. Log in with your credentials
4. Go to **Workflows** → **Import from File**
5. Select a JSON file from this directory

## Templates

| File | Purpose | Use Cases |
|------|---------|-----------|
| `webhook-trigger.json` | Webhook → process → notify | Trigger actions from external services |
| `scheduled-task.json` | Cron → process → output | Periodic data collection, cleanup, reporting |
| `api-integration.json` | HTTP Request → transform → store | Bridge external APIs with local services |

## Requirements

- n8n 2.x (ODS bundles n8n 2.41.6)
- ODS running, with the services a template calls enabled

## How templates reach ODS services

n8n 2.x blocks `$env` in expressions by default (`N8N_BLOCK_ENV_ACCESS_IN_NODE=true`),
so the templates do not read environment variables. They call services by their
Docker Compose service name and container port on the ODS network, for example
`http://llama-server:8080/v1/chat/completions` for the local LLM and
`http://dashboard-api:3002/health` for the dashboard API. Change a URL in the node
if your service runs elsewhere.

The LLM templates call llama-server directly, which needs no API key. To route
through LiteLLM instead (`http://litellm:4000/v1/chat/completions`, model
`ods/current`), add a Header Auth credential with `Authorization: Bearer <LITELLM_KEY>`
to the request node.

## Credentials

Templates never embed secrets. After import, create these n8n credentials:

- **ODS Webhook Header Auth** (Header Auth): the header name and value callers must
  send to the template's webhook.
- **Form Storage API Key** (Header Auth): used by `form-processing.json` to call your
  storage endpoint.

URLs on `example.invalid` are placeholders; a sticky note in the workflow marks
them. Replace them with your own endpoint before activating the workflow.

## Customization

Edit workflows in n8n's visual editor:
- Update credentials with your API keys
- Adjust node parameters for your use case
- Test before saving

## Troubleshooting

- **Connection refused**: Ensure n8n container is running (`docker ps | grep n8n`)
- **Node errors**: Check credentials and network connectivity
- **Port conflicts**: Verify n8n port matches your installation (`${N8N_PORT:-5678}`)
