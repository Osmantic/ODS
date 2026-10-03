# changedetection.io for ODS

Track selected page text and JSON/API responses, retain history and inspect diffs.
This is a web change monitor, distinct from Uptime Kuma's availability checks.
The recipe uses the real HTTP fetcher; JavaScript rendering, screenshots and
browser steps require a separately configured compatible browser service.

## Deployment

- Apache-2.0 upstream 0.60.7, official image pinned by digest with amd64/arm64/ARM
  manifests. Windows and macOS use Docker's Linux engine; Linux uses the same
  named-volume recipe. HTTP monitoring requires no GPU or loaded model.
- Small derived layer owns `/datastore` as UID/GID 1000. The service runs nonroot
  with no-new-privileges, one CPU and 1 GiB memory, without host-directory or
  Docker-socket access.
- Set required `CHANGEDETECTION_PASSWORD` in ODS extension configuration, then
  open `http://localhost:11039/login`. The startup adapter derives the exact
  upstream salted PBKDF2-SHA256 format in memory and removes the cleartext variable
  from the child environment. Docker's configured environment still contains the
  original secret; this is not a secret-store replacement.
- The environment-managed password takes precedence over settings in the UI.
  Change it through ODS and recreate the service to rotate it. Do not expect a UI
  password change to override it.
- `CHANGEDETECTION_BASE_URL` controls links; update it if you change the local
  published port. `CHANGEDETECTION_TIMEZONE` defaults to UTC for watch schedules.

## First use and persistence

Fresh installation starts with **no watched URLs**. A build-time edit, checked
against the pinned source, disables the two upstream example watches. Existing
watches are not removed during upgrades. Add a real public page yourself, select
the HTTP fetcher, and inspect the first snapshot before enabling any alerts.
Two workers and a minimum 60-second recheck interval bound default polling load.

The named `changedetection-data` volume preserves configuration, watches, stored
snapshots, diffs, application keys and API token across restarts. Back it up before
upgrading; preserve UID/GID 1000 on restore. Disabling the service must retain it.
No notifications are configured or sent by this integration. Configure any chosen
notification destination explicitly in the application. API access uses the
separate generated token exposed in its settings, not the web password.

File URLs and private/reserved network addresses remain disabled. This recipe
monitors public sources; it does not grant watched pages access to other ODS
services. Version telemetry and unconfigured LLM features are disabled. No model
name, context size or external inference provider is selected. Dynamic pages need
additional browser configuration; the recipe does not silently substitute an
unverified Chrome image or grant SYS_ADMIN privileges.

## Validation limits

`/login` readiness checks the web listener, not worker progress or successful
fetching. Image provenance, upstream password contract, ODS staging and Compose
configuration are checked separately. **Build/application runtime is pending.**
Local acceptance should verify password login, capture a page, inspect its diff,
restart and confirm history persists. No live website watches, browser processes,
LLM inference or notification delivery were run while preparing this recipe.

## Sources

- [Pinned upstream project and license](https://github.com/dgtlmoon/changedetection.io/tree/0.60.7)
- [Upstream deployment options](https://github.com/dgtlmoon/changedetection.io/blob/0.60.7/docker-compose.yml)
- [Password protection](https://github.com/dgtlmoon/changedetection.io/wiki/Password-protection)
- [API reference](https://changedetection.io/docs/api_v1/)
