# FreshRSS — research subscriptions

Install and enable in ODS Extensions, then open `http://localhost:11029`. Complete the upstream setup wizard, choose **SQLite**, and create a personal administrator account with a strong password. There is no pre-created account or shared default password. Keep authentication enabled.

Use categories to separate project sources, import existing subscriptions as OPML, and add the actual RSS/Atom URLs from the sources you follow. Export OPML to keep a portable subscription list. Articles, read/starred state and configuration remain in the data volume; OPML alone is not a complete backup. This is a distinct project from the catalog's Miniflux integration, with its own PHP extension/theme ecosystem and independent data.

## ODS configuration

- `FRESHRSS_PORT` defaults to host port 11029, bound to loopback. Internal Apache remains on port 80. No GPU or loaded model is involved.
- `FRESHRSS_TIMEZONE` defaults to `UTC`; use an IANA name appropriate for the owner, such as `America/Sao_Paulo`.
- `FRESHRSS_CRON_MIN` defaults to `7,37`, refreshing subscribed feeds twice hourly. An explicitly empty value disables the built-in schedule; manual refresh remains available. Choose a reasonable interval for the source servers and number of feeds.
- `freshrss-data` contains application settings, SQLite and article state. `freshrss-extensions` retains installed FreshRSS add-ons. These add-ons are not separate ODS catalog entries.
- Initialization follows the official image: its parent process configures cron, Apache and volume ownership; web request workers use `www-data`. Do not force an arbitrary container UID and break that initialization. No privileged mode, host socket or host-directory mount is used.

The official image supports amd64, arm64 and ARMv7. Windows/macOS use Linux containers through Docker Desktop; Linux uses Docker Engine. Named volumes keep the same storage layout across those hosts. Allow more memory and disk for large subscription lists and long retention; the recipe sets a 512 MB container limit rather than promising unlimited capacity.

## Account, connectivity and recovery

Initial credentials are entered in FreshRSS itself. This recipe intentionally does not expose upstream's shell-expanded `FRESHRSS_INSTALL`/`FRESHRSS_USER` command strings as credential configuration. Changing container environment variables later is not an account-password reset.

Feed fetching needs access to the selected source URLs. Keep upstream internal-host restrictions intact. If a project genuinely needs a private feed, configure only its exact host/port in FreshRSS system settings; do not enable a wildcard allowlist. Browser/client APIs are opt-in in the application and require their own credentials. No subscription, external notification or model interaction is fabricated by installation.

The local recipe disables trust in incoming proxy headers. Remote access requires an intentional TLS/authenticated reverse-proxy setup and a matching FreshRSS base URL; do not merely widen the bind address. Before moving machines, stop the service and back up both complete named volumes. Restore them together with the same pinned image before attempting an upgrade. Disabling the extension preserves data.

## Validation boundaries

The PHP/cURL health check has a timeout and rejects HTTP error status codes. It confirms the web listener, including the first-run wizard; it does not prove that account setup, cron refresh, feed parsing or persistence succeeded. After setup, add a real source, refresh it, mark an article read, restart and verify that the state remains.

Registry manifests, official entrypoint, PHP/cURL availability and configuration were inspected. Application runtime remains pending; configuration and installation-staging checks do not replace those user flows.

- [Versioned source and AGPL-3.0 license](https://github.com/FreshRSS/FreshRSS/tree/1.30.0)
- [Official image](https://github.com/FreshRSS/FreshRSS/blob/1.30.0/Docker/Dockerfile)
- [Initialization behavior](https://github.com/FreshRSS/FreshRSS/blob/1.30.0/Docker/entrypoint.sh)
