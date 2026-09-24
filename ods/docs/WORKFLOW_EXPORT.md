# Export an installed workflow

Save a portable copy before editing an installed n8n workflow or moving it to another ODS box. Read `/api/workflows` and use the selected item's `n8nId` (not its catalog ID):

```bash
curl --fail --header "Authorization: Bearer $DASHBOARD_API_KEY" \
  "http://127.0.0.1:3002/api/workflows/n8n/WORKFLOW_ID/export" \
  --output workflow.json
```

The authenticated endpoint downloads `name`, `nodes`, `connections` and `settings` from the installed definition. It excludes execution state, sharing metadata, activation state and the source instance's workflow ID. It does not read the bundled template and never changes or disables the source workflow. Import the JSON through n8n's workflow import interface, then review credentials and activate explicitly when ready.

Node parameters and credential references are part of the definition: review them before sharing. This is not a credential backup or a secret-redaction tool; values manually embedded in node parameters are included. Credential-store secrets and execution history are not fetched. Responses use `Cache-Control: no-store`.

Missing workflows return 404, malformed or failed upstream responses return 502, and the ten-second upstream deadline returns 504. The exporter refuses upstream redirects. The contract uses the public GET workflow API of the shipped [n8n 2.6.4](https://github.com/n8n-io/n8n/blob/n8n%402.6.4/packages/cli/src/public-api/v1/handlers/workflows/spec/paths/workflows.id.yml). Automated tests mock this transport; a live export/import round trip remains unverified.
