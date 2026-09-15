# Gatus: local service history

Install **Gatus (Local Service History)**, generate a local password hash, set
`GATUS_PASSWORD_BCRYPT`, then enable the extension. Open `http://localhost:8102`
and authenticate as `ods` when the native UI requests detailed status data.
`GATUS_PORT` changes this loopback port. `BIND_ADDRESS` does not publish Gatus
to the LAN.

The pinned Gatus 5.36.0 image checks the Docker chat and inference listeners
at `open-webui:8080/health` and `llama-server:8080/health` every 30 seconds.
It records successful, failed and recovered probes locally. An HTTP 200 is an
availability signal; it does not prove a model can answer correctly. Stopped
or differently located services appear unhealthy until their targets are edited.
No chat service, model or inference server is enabled automatically.

## Generate the native credential locally

Gatus expects a URL-safe base64 encoding of a bcrypt hash, not a plaintext
password. The bundled helper asks for a password twice without echoing it and
prints only the native hash. Use 16–72 UTF-8 bytes; store the password in your
password manager and paste the generated 80-character hash into ODS Settings.
Never submit your password to an online hash generator.

The helper uses Python and `bcrypt==5.0.0` in a separate local environment.
From the ODS installation directory on Linux/macOS/WSL:

```bash
python3 -m venv .gatus-password-env
.gatus-password-env/bin/pip install bcrypt==5.0.0
.gatus-password-env/bin/python data/user-extensions/gatus/hash-password.py
```

On native Windows, use the corresponding environment's `Scripts/python.exe`
and `Scripts/pip.exe`. This is a local maintenance helper; Gatus itself runs
its upstream binary without Python or extra runtime dependencies. Keep the
hash private too: an attacker can attempt offline guesses against it.

Native Basic authentication protects the detailed endpoint/suite status APIs.
**Gatus deliberately leaves its HTML, UI configuration, health probe, badges,
uptime and response-time endpoints public.** Basic auth does not make every
status datum private. Loopback publication limits host access, while other
containers on the ODS network can still reach these native public endpoints.
No metric, alerting, external-endpoint ingestion token, remote instance or
outbound notification integration is configured by this recipe.

To rotate the password, generate a new hash, update `GATUS_PASSWORD_BCRYPT`
and recreate the service. Verify the old password is rejected and the new one
can read detailed status. Basic auth is checked on each request; there is no
recipe-created session token to revoke. Missing/empty hashes block Compose
activation. Malformed base64 stops native startup; valid base64 containing an
invalid bcrypt hash denies authentication instead of granting access.

## Configuration and lifecycle

ODS copies the bundled `config/gatus/config.yaml` before activation; the
Compose bind refuses to create a directory when the source file is missing.
The native configuration expands `GATUS_PASSWORD_BCRYPT` in memory. The YAML
contains the environment reference, not the password hash itself.

For custom endpoints, disable Gatus, back up the active configuration and edit
`data/user-extensions/gatus/config/gatus/config.yaml`, then enable and verify
the resulting checks. ODS synchronizes the installed definition into
`config/gatus/config.yaml`. Keep local edits separately before reinstalling or
updating the extension. Native/external inference deployments must set their
actual reachable health URL; the default name describes the Docker backend.
Changing a monitored group/name changes its native history key.

`data/gatus` holds SQLite history. ODS prepares UID/GID 1000 ownership. The
container has a read-only filesystem, 64-MiB temporary storage, dropped
capabilities, one CPU and 256 MiB memory. Native result/event windows are
100/50; these are retained history windows, not a complete disk quota.
The native scratch image has no shell or HTTP probe executable, so the recipe
does not declare an unusable Docker healthcheck. ODS requests `/health`.
Container-running status alone is not readiness or target availability.

Disable before cold-copying the whole `data/gatus` directory and active
configuration. Preserve ownership, your password hash and endpoint names.
Restore with the same pinned image, recreate and verify old failure timestamps
alongside fresh successful checks. Disable/remove retains history. Keep a
compatible cold backup before upgrades; changing the image alone cannot undo
a database migration. Reverting the hash is possible but re-enables the old
password, so treat that as an explicit credential decision.

## Qualification

```bash
python -m pytest tests/test_gatus.py -q
ODS_TEST_GATUS=1 python -m pytest tests/test_gatus.py -q
cd extensions/services/dashboard-api
pytest tests/test_gatus_install.py tests/test_extensions.py tests/test_host_agent.py -q
```

The live fixture preserves the production target names, paths and conditions,
using an isolated BusyBox HTTP server behind both DNS aliases and a shorter
probe interval. It exercises healthy → failed → recovered history, wrong and
missing authentication, native public routes, hash rotation, cold restore,
invalid-hash rejection and recovery. The local hash helper is exercised through
its CLI. The authenticated ODS install test verifies configuration sync and
storage ownership before host activation.

Evidence covers Linux/amd64 Docker Desktop on WSL Ubuntu. Actual running ODS
chat/inference hosts, native macOS/Linux hosts, browser rendering, large fleets,
long retention, notifications, OIDC/TLS, upgrades and a complete network-egress
audit remain unqualified. The fixture proves HTTP availability/history behavior,
not inference correctness or production-host uptime.

Native contracts: [5.36.0 release](https://github.com/TwiN/gatus/releases/tag/v5.36.0),
[authentication and public routes](https://github.com/TwiN/gatus/blob/v5.36.0/api/api.go),
[Basic authentication](https://github.com/TwiN/gatus/blob/v5.36.0/security/config.go),
[SQLite storage configuration](https://github.com/TwiN/gatus/blob/v5.36.0/storage/config.go).
