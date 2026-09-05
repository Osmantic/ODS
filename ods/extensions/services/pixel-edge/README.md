# Pixel Edge

`pixel-edge` is ODS's internal OpenAI-compatible adapter between Open WebUI and a host-installed Pixel. It exposes no host port and never receives Pixel's OpenClaw operator token.

The trust chain is deliberately split:

1. Open WebUI authenticates here with the generated, ODS-scoped `PIXEL_OPENWEBUI_KEY`.
2. This container accepts only `GET /v1/models`, `GET /v1/activity`,
   `POST /v1/chat/completions`, `POST /v1/chat/cancel`, and authenticated
   `GET /preview/<site-id>/<path>` requests from Dashboard nginx. Chat fixes
   the model to `pixel/default`, strips browser credentials and every
   `x-openclaw-*` header, and connects to the private ingress Unix socket. The
   preview route connects to a separate read-only host Unix socket and relays
   only immutable content-addressed files with a script-capable opaque CSP
   sandbox. The activity route exposes only an authenticated count so ODS can
   refuse a model switch while Dashboard or Open WebUI Pixel work is active.
3. The host `pixel-agent` integration owns that socket and is the only component that reads and injects Pixel's full gateway credential.

Pixel is a single-owner agent runtime. The default route is therefore intended for the ODS owner surface, not an untrusted multi-user Open WebUI deployment. Hermes and OpenCode remain available as explicit rollback paths.

Both socket directories are mounted read-only and have no TCP publication.
`PIXEL_INGRESS_GID` grants the non-root container process access without making
either socket world-readable. Preview relay calls use the Dashboard API key as
a distinct server-injected credential; it is never sent to the browser.

Chat requests keep bounded 33-minute total and no-first-byte budgets. This is
one minute longer than the private host ingress and three minutes longer than
OpenClaw's ODS-managed provider timeout, allowing CPU-only first-turn prefill
without making any intermediate proxy the first timeout authority.

Run the focused offline tests from this directory:

```bash
python3 -m unittest discover -s tests -v
```
