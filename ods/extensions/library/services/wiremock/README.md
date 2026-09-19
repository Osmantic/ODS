# WireMock — local API fixtures

Use repeatable HTTP responses to develop n8n workflows or agent integrations
without calling the real upstream API. This optional service does not redirect
any existing provider or workflow automatically.

## Enable and create a fixture

Install **WireMock** from Extensions, supply a strong `WIREMOCK_PASSWORD`, then
enable it. The administrator username is `ods`. The default host port is 8099;
change `WIREMOCK_PORT` if it is occupied. API documentation is at
`http://localhost:8099/__admin/docs` and prompts for administrator credentials.
Use a password without a colon, for example an `openssl rand -hex 24` value.

From a terminal, create a persistent response (curl prompts for the password):

```bash
curl --fail --user ods -H 'Content-Type: application/json' \
  http://localhost:8099/__admin/mappings \
  --data '{"persistent":true,"request":{"method":"GET","url":"/fixture/status"},"response":{"status":200,"jsonBody":{"ready":true}}}'
curl --fail http://localhost:8099/fixture/status
```

An ODS container on `ods-network` uses `http://wiremock:8080/fixture/status`.
Unmatched requests return 404. Use mappings with `status: 429`, `status: 503`,
or `fixedDelayMilliseconds` to exercise caller failure behavior. The returned
mapping `id` can be deleted via authenticated `DELETE /__admin/mappings/{id}`.
Keep credentials in the administrator client, not in mock response bodies.

## Access and limits

Administration requires Basic authentication; **stub responses and files are
public to clients that can reach the listener**. Use synthetic fixtures rather
than real secrets or private API recordings. The host binds to loopback by
default. An explicit `BIND_ADDRESS` override changes that exposure; Basic auth
is not encrypted, so use a trusted TLS proxy for remote administration.

The recipe disables response templating and extension scanning. There is no
default proxy or recording configuration. An authenticated administrator can
add proxy mappings that make network requests, so use trusted fixture authors:
this service is not an outbound-network sandbox. It retains at most 100
request journal entries in memory and limits JVM heap to 512 MiB inside a 1 GiB
container. Clear journal entries through authenticated `DELETE /__admin/requests`.
The journal is for recent diagnostics and is not durable. The public
`/__ods_status.json` probe establishes HTTP reachability, not fixture correctness.

## Persistence, rotation, and backup

`data/wiremock/mappings` contains persisted mappings; `data/wiremock/__files`
contains response files. API mappings need `"persistent":true` (or an explicit
admin save operation) to survive replacement. Files under `__files` may also be
served directly without a mapping. The ODS host agent prepares UID 1000 storage.

Disable the extension, copy the entire `data/wiremock` directory, and re-enable
it for a cold backup. Restore while stopped with UID 1000 ownership. Reverting
the recipe does not revert fixtures; retain the backup before changing images.
Disabling retains this directory. Change `WIREMOCK_PASSWORD` in configuration
and recreate the container to rotate access; it is startup configuration, not a
database seed. Stop or redirect clients before rotating the shared admin account.

The opt-in test `ODS_TEST_WIREMOCK=1 python -m pytest ods/tests/test_wiremock.py -q`
qualifies the pinned image through real HTTP: auth, persistence, failure
responses, rotation, and cold restore. Linux amd64 is
qualified; ARM and native macOS/Windows remain unverified.

See [WireMock Docker](https://wiremock.org/docs/standalone/docker/) and
[standalone options](https://wiremock.org/docs/standalone/java-jar/).
