# Scoped service resource snapshots

Use the authenticated resource API to monitor a selected subsystem:

```bash
curl --fail --header "Authorization: Bearer $DASHBOARD_API_KEY" \
  'http://127.0.0.1:3002/api/services/resources?service=llama-server&service=open-webui&include_disk=false'
```

Repeat `service` for up to 50 IDs. Duplicates are collapsed and totals cover only returned services. Omit the filter for the existing full snapshot. Invalid IDs return 400, unknown IDs return 404, and invalid query types/oversized lists return 422. Use IDs from the unfiltered response, not container names or display labels.

`include_disk=false` skips directory traversal, including on a cold cache, for lightweight CPU/RAM polling. Each `disk` value and `totals.disk_data_gb` are null, not zero; `scope.disk_included` is false. Orphaned data-only entries are available only with disk collection enabled. The default still includes disk usage.

The host-agent container cache remains a full snapshot (20-second TTL), and the full disk cache retains its 60-second TTL. Filtering never replaces either cache with a subset. This option reduces disk collection work, not the host agent's container-stat query. Resource availability and Docker Desktop caveats remain those of the original endpoint; missing metrics are not proof that a service is stopped. No lifecycle actions are issued.

Tests exercise authenticated HTTP responses, deduplicated totals, full-snapshot reads after filtered requests, cold-cache disk omission and auth/validation failures. Live Docker CPU/RAM collection is not exercised by those transport-mocked tests.
