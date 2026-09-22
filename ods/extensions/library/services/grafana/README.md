# Grafana OSS — local observability dashboards

Grafana 13.2.2, AGPL-3.0, official `grafana/grafana` OSS image pinned by digest. This is not the Enterprise image. Upstream: https://github.com/grafana/grafana/tree/v13.2.2. Image documentation: https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/.

## First setup

Set distinct 64-hex secrets `GRAFANA_ADMIN_PASSWORD` and `GRAFANA_SECRET_KEY` through ODS configuration. Open `http://localhost:11100/` and sign in as `ods` using the administrator password. Public registration and anonymous access are disabled. No known default password is accepted by this recipe.

The administrator password initializes only a new database. For an existing installation, change it through the authenticated UI or Grafana's documented administrator recovery flow; changing the environment alone does not reset it. Keep the encryption key stable across recreation and restore, because it protects stored data-source credentials. Rotation requires Grafana's supported migration process, not merely replacing the environment variable.

## Connect real project data

Create a data source explicitly in Grafana and supply the correct service URL/credentials. Other services on `ods-network` are addressed by their service names and internal ports; `localhost` in Grafana refers to this container. Start with a read-only database/metrics account appropriate to the selected source. Import or create dashboards against that real source.

The extension does not automatically scrape ODS, connect a database, enable alert delivery, send notifications or modify the selected AI model. An empty installation has no project dashboards or invented metrics. Grafana visualizes data; it is not a replacement for a metrics collector. No Cloud account, SMTP server or external AI service is configured.

Usage reporting, update checks, plugin update checks, default plugin preinstallation and plugin catalog administration are disabled. Bundled OSS data sources/panels remain available. Additional plugins need a separately reviewed integration with pinned versions and compatible licenses; they are not silently downloaded at startup.

## Storage and compatibility

The named `grafana-data` volume persists `/var/lib/grafana` (SQLite state, users, dashboards, plugins). Back it up consistently while stopped, alongside the encryption secret. Preserve UID 472 ownership on restore. Do not delete it to repair a login problem. Read-only root, bounded temporary storage, console logs, one GiB memory and two CPUs. Published port is loopback-only; remote access requires deliberately configured HTTPS/root URL and authentication.

Official image supports Linux amd64/arm64 for Docker on Windows/Linux/macOS. No GPU or model dependency. `/api/health` checks native HTTP/database readiness, not a working dashboard or external data-source connection. Image build, first login, credential encryption, actual queries, restore and platform runtime remain pending; no service/model started during recipe preparation.
