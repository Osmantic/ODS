# Gatus for ODS

Availability and response-time monitoring for an HTTP endpoint chosen explicitly
at installation. This is the Apache-2.0 Gatus application, not a generated status
card. It stores real observations in SQLite and exposes its native dashboard.

## Setup

Install `gatus` and set `GATUS_ENDPOINT` to an HTTP(S) URL reachable from its
container. Choose the actual health URL for your service, which should return
HTTP 200. Monitoring begins when enabled, once per minute with a 10-second timeout.
Redirects count as failures rather than silently following a login page.

Open `http://localhost:11064`; `GATUS_PORT` changes the loopback host port.
Container localhost refers to Gatus itself. Use an actual ODS service DNS name
and internal port on `ods-network`, or a reachable remote address. No sample site,
public API, notification recipient or monitoring target is installed by default.
Do not put passwords or private tokens in the target URL. This initial endpoint
configuration supports unauthenticated HTTP(S) checks; it does not supply Portal
credentials or claim functional application verification from a status code.

The dashboard has no authentication and is bound to host loopback. Do not expose
it publicly without configuring access control. Other containers sharing
`ods-network` can also reach its internal port. Metrics and alerting providers are
not configured. Native Gatus supports more advanced checks, but adding endpoints,
authentication or alerts requires an explicit recipe/configuration update.

## Data and platforms

`gatus-data` retains `/data/gatus.db` across restarts. Back it up with Gatus stopped.
Changing the target does not erase earlier records automatically. The launcher
builds configuration as JSON and escapes dollar signs before Gatus's environment
expansion, preserving literal URLs without YAML interpolation. Generated config
is temporary; the selected endpoint remains in ODS's saved extension settings.

Pinned multiarch scratch image, UID 65532, read-only filesystem and a bounded
temporary directory. The static helper forwards termination to Gatus. Use Docker
Engine on Linux or Docker Desktop Linux containers on Windows/macOS; no GPU,
selected model, host socket or inference server is involved.

Health checks use native `/health`, which reports Gatus server readiness. This is
separate from the monitored endpoint's status and does not mean that endpoint is
healthy. Image build, actual probing/history, dashboard and restore/platform
behavior remain runtime-pending. No application containers were started.

Source: https://github.com/TwiN/gatus/tree/v5.36.0
