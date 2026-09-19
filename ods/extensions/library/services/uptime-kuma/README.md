# Uptime Kuma service monitoring

Track service availability and retain incident history in the local ODS stack.
Install this optional recipe from Extensions (or copy this directory to
`extensions/services/uptime-kuma`), then run `ods enable uptime-kuma`.

Open `http://localhost:8102` and create the administrator account before exposing
any remote access. The initial setup page is loopback-only by default and has no
shipped password. Use `UPTIME_KUMA_WEB_PORT` to change the published web port;
the internal listener remains 3001. Keep authentication enabled and use the
installation's protected access path when administering from another device.

## First monitor

Choose **Add New Monitor → HTTP(s)** and supply the target's internal ODS URL,
for example `http://dashboard-api:3002/health` if that endpoint is available in
your active installation. Verify the exact service URL/port in its manifest.
Use the endpoint's documented credentials or expected status codes where needed;
a login page or listening socket alone does not prove application readiness.

Start with a 60-second interval. Notifications such as ntfy are configured
explicitly in Kuma; this recipe creates no default alert destination or public
status page. Targets and notification providers may make external requests.
Upstream also checks `uptime.kuma.pet` for release information by default; turn
off update checking in its settings if you require that request disabled.
This is local storage and administration, not a network isolation guarantee.

## Runtime and persistence

The pinned **2.5.4-slim** image uses SQLite in `data/uptime-kuma`. A one-shot
initializer, using the same image with no network, gives only that data
directory to UID/GID 1000. The application then runs as that user with no Linux
capabilities and a read-only root filesystem. Neither container mounts the
Docker socket. The app is limited to 512 MiB and one CPU; reduce monitor count
or adjust the limit deliberately for larger installations.

The slim image omits the bundled browser engine and embedded MariaDB. This
recipe validates HTTP monitoring; Docker, browser-engine, privileged ICMP,
tunnel and other specialized monitors are outside its validated scope. The
container healthcheck reports Kuma's web availability, not the health of its
configured targets.

To back up, stop the application and copy the **whole** `data/uptime-kuma`
directory with ownership intact. Include SQLite WAL files and database config.
Restore while stopped to the same pinned image first. Disable or revert the
recipe to roll back; retain the data until recovery is verified. Do not repoint
this fresh-install recipe at an existing database from a different Kuma version
without following upstream's migration instructions.

## Validation

The opt-in Chromium test creates an administrator through the UI, records real
HTTP Up/Down monitor states, rejects anonymous management access, recreates the
container, and signs in again to inspect the persisted monitor. It runs the
copied install fragment with isolated names and an ephemeral loopback port.

```bash
python -m pip install 'playwright==1.62.0'
python -m playwright install chromium --with-deps
ODS_TEST_UPTIME_KUMA_BROWSER=1 pytest -q tests/test_uptime_kuma_extension.py
```

Live host coverage is Linux amd64 Docker/Chromium on WSL. ARM, macOS, native
Windows data mounts, rootless Docker, external notifications and backup restore
remain unverified. [Pinned release](https://github.com/louislam/uptime-kuma/releases/tag/2.5.4),
[image contract](https://github.com/louislam/uptime-kuma/blob/2.5.4/docker/dockerfile),
[database selection](https://github.com/louislam/uptime-kuma/blob/2.5.4/server/setup-database.js).
