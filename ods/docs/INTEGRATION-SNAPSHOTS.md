# Integration snapshots

Open **Settings → Integrations** and choose **Download snapshot** to save
`ods-integrations.json`. The full Integrations map has the same action. This is
a local download of the last successful status response; it makes no new API
request. Search and status filters do not truncate the exported inventory.

The version 1 JSON receipt contains:

- `capturedAt`: browser UTC time when the last successful response was processed.
- `refreshFailed`: whether a subsequent status refresh failed. An old snapshot
  retains its original capture time instead of appearing freshly measured.
- `services`: reported IDs, names, statuses, published ports and map categories.
- `knownDependencies`: the dashboard's known connections between those services.
  These are configured map relationships, not observed network traffic or proof
  that an application-to-application request succeeded.

Use a snapshot to compare service availability before and after a change or to
attach a small inventory to an incident. Public service URLs, credentials, logs,
prompts and configuration contents are excluded. Names and ports still describe
your installation; review the file before sharing it. No download is offered
until at least one service has been reported. The file is diagnostic only and
cannot be imported to change or restore service configuration.
