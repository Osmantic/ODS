# go-httpbin for ODS

A local HTTP test service for debugging project API clients. Echo request data,
exercise redirects and failures, or inspect delayed and streaming responses.
Unlike WireMock, it provides protocol endpoints rather than user-defined stub
mappings. It does not answer Portal chat messages or replace model responses.

## Use from your project

Host clients use `http://localhost:11044`. Containers already attached to
`ods-network` use `http://go-httpbin:8080`; their `localhost` refers to themselves.
Examples below are **opt-in tests**, not operations run during installation:

```text
GET /get?project=example
POST /anything
GET /status/503
GET /delay/2
GET /stream/3
GET /redirect/2
```

POST a synthetic JSON payload to `/anything` and verify the response reports the
method, headers and payload your client actually sent. Use `/status/503` to
exercise retry handling and `/delay/2` to inspect timeout behavior. These results
demonstrate client behavior against a test server, not a production API or model.

This is an API-only ODS entry, so it has no invented application launch button.
The upstream root page still documents available endpoints if opened manually.

## Deployment and limits

- MIT upstream **2.25.0**, official image pinned for linux/amd64 and linux/arm64.
  Windows/macOS use Docker's Linux engine, Linux uses the same Compose recipe.
- The upstream distroless server runs as UID/GID 65532 with a read-only root and
  no-new-privileges. A small, separately pinned Go build stage adds an HTTP health
  executable; it does not install a runtime shell or use a fake `curl` command.
- Host publishing is loopback-only. Limits: 128 MiB RAM, half a CPU, 1 MiB request
  or generated response, and ten seconds for configurable response durations.
  Redirect-to destinations are limited to localhost, 127.0.0.1 and go-httpbin.
- No storage volume is needed: echoed requests and test responses are not a
  persistent project database. No credentials, model settings or external proxy
  are configured. Request logging is reduced to WARN.

The service has no global account authentication and intentionally echoes request
data. Use test credentials and synthetic payloads; do not substitute it for an
authenticated production endpoint. The `/env` feature only exposes upstream's
explicitly prefixed environment values; this recipe defines none of those.

Readiness probes only `/status/200`, with a timeout and no redirects or proxy.
It does not prove that your project's client tests or streaming parser pass.
**Docker build and application runtime remain pending.** Image provenance,
configuration and ODS staging are checked separately. No HTTP examples above
were sent during recipe preparation.

- [Pinned source/license](https://github.com/mccutchen/go-httpbin/tree/v2.25.0)
- [Usage and configuration](https://github.com/mccutchen/go-httpbin/blob/v2.25.0/README.md)
