# Uptime Kuma — service monitoring

Keep uptime history, investigate response times and configure notifications for selected ODS services or websites. This is a separate monitoring application; it does not replace the ODS lifecycle/readiness system and does not claim that a responding AI server produces correct answers.

## First use

Install and enable Uptime Kuma in ODS Extensions, then open its launch action at `http://localhost:11027`. Create the administrator account in the initial setup screen. The database is already configured as local SQLite; no external database or default password is supplied. Enable two-factor authentication in account settings if desired.

Add HTTP monitors only for services you actually installed and enabled. These addresses use the shared Docker network, not the browser's host port:

| Installed ODS extension | HTTP monitor URL | What the check observes |
| --- | --- | --- |
| SearXNG | `http://searxng:8080/healthz` | Search service health endpoint |
| Gotenberg | `http://gotenberg:3000/health` | Document conversion service readiness |
| Penpot | `http://penpot:8080/readyz` | Frontend-to-backend readiness path |

Start with a 60-second interval and two retries, adjust to your needs, and pause monitors during planned service shutdowns. A disabled extension will otherwise correctly appear down. These examples are not automatically imported, activated or counted as installed dependencies.

`localhost` inside Kuma refers to Kuma itself. Use the extension's Docker service name and internal port for container targets. Host-native services need an explicitly configured reachable host address; do not assume `host.docker.internal` exists on every Linux installation. Remote sites use their real HTTPS URLs with certificate verification enabled.

Notifications are opt-in: configure the provider and its credentials in Kuma, use its test action, and attach it to selected monitors. No email, webhook or external message is sent by installing this recipe. Status pages are also explicitly created by the owner. This recipe does not expose a status page publicly.

## Platform, data and limits

- Official `2.5.5-rootless` image pinned by digest, with amd64, arm64 and ARMv7 manifests. Windows/macOS run Linux containers through Docker Desktop; Linux uses Docker Engine. No model or GPU is needed.
- Runtime uses the upstream `node` UID/GID 1000. The image prepares `/app/data` for that user; a fresh Docker named volume preserves that ownership without host-path fixes. Do not reuse a volume from a rootful install without checking its ownership first.
- `uptime-kuma-data` stores SQLite, settings, account information, monitor history and notification configuration. It must be local storage; upstream does not support NFS for this database. Disable/stop preserves it. Stop the extension before copying the complete volume for a consistent backup. Treat backups as sensitive because they include monitoring credentials.
- Embedded MariaDB is explicitly disabled. SQLite is an upstream-supported database and is selected through `UPTIME_KUMA_DB_TYPE`. Large monitor fleets may need a deliberate database migration; changing an environment value is not a migration.
- The UI binds to loopback only. `UPTIME_KUMA_PORT` changes the host port, while the internal application remains on 3001. Remote access requires an intentional authenticated reverse-proxy setup with WebSocket support.
- No host Docker socket, host filesystem or privileged mode is mounted. HTTP/TCP monitoring works over the network. Docker-container monitors require a separately authorized Docker endpoint; the recipe does not grant host control. ICMP support can depend on the host/container capability configuration; prefer HTTP/TCP when it is unavailable.
- The configured memory limit is 1 GB. Monitor count, history retention, browser monitors and response sizes affect actual resource needs. Choose retention in the application rather than assuming unlimited history.

## Health and validation

The recipe replaces upstream's bundled health checker, which accepts any completed HTTP response, with a bounded Node HTTP probe that rejects 4xx/5xx responses and connection errors. It checks the local UI listener, not every monitor or notification delivery. Docker's outer timeout also bounds stalled responses. Initial account setup can still be required while the listener is healthy.

Image metadata and versioned database setup/Docker sources were inspected. ODS schema, Compose and installation-staging checks are separate from application runtime. Browser setup, monitor transitions, notification delivery, persistence and upgrades remain runtime checks for the target computer; no application was started during recipe preparation.

- [Project and MIT license](https://github.com/louislam/uptime-kuma/tree/2.5.5)
- [Official image build](https://github.com/louislam/uptime-kuma/blob/2.5.5/docker/dockerfile)
- [Database setup implementation](https://github.com/louislam/uptime-kuma/blob/2.5.5/server/setup-database.js)
- [Official deployment instructions](https://github.com/louislam/uptime-kuma#how-to-install)
