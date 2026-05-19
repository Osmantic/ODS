# n8n Workflow Templates

This directory contains import-ready n8n workflow JSON files for Dream Server.

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

- n8n v1.0+
- Dream Server running (for service discovery via `host.docker.internal`)

## Customization

Edit workflows in n8n's visual editor:
- Update credentials with your API keys
- Adjust node parameters for your use case
- Test before saving

## Troubleshooting

- **Connection refused**: Ensure n8n container is running (`docker ps | grep n8n`)
- **Node errors**: Check credentials and network connectivity
- **Port conflicts**: Verify n8n port matches your installation (`${N8N_PORT:-5678}`)
