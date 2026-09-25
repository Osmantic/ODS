# Grist Community

Relational spreadsheets with Python formulas, reference columns, linked table/chart views and portable `.grist` documents. Use it to maintain project datasets or structured planning data, with CSV/Excel import and exports instead of generating another project database from scratch.

## Distribution and startup

This recipe uses **gristlabs/grist-oss 1.7.19**, pinned by manifest digest, containing the Apache-2.0 community code. It deliberately does not use the default `grist` or enterprise image, which include additional edition components. Upstream entrypoint, database initialization and server command are preserved; `/persist` is prepared for the image's `grist` user.

Set `GRIST_OWNER_EMAIL` to the local document owner's identity and `GRIST_SESSION_SECRET` to a long random signing secret retained across restarts. The owner email is a local identity, **not authentication**: this deployment auto-selects that owner. No email is sent. The local UI is `http://localhost:11082` (or the chosen `GRIST_PORT`). Other ODS containers can reach `http://grist:8484`.

Published access is loopback-only. Other processes on the computer and containers in `ods-network` may access documents as the owner. A shared or remote deployment needs a real upstream-supported authentication provider and matching external URL; changing the published address alone is insufficient.

## Project use and persistence

Import CSV/Excel data, define typed columns and references, then add formulas and linked views. Download complete `.grist` documents to retain structure and history; CSV exports contain table data rather than the full application. The REST API is available upstream for explicitly configured clients. This recipe does not create API keys, mount Playground or automatically grant Portal access to documents.

The `grist-data` volume contains `/persist/home.sqlite3`, document files under `/persist/docs`, settings and local home data. Back up the whole volume with the service stopped or use document downloads. Do not copy a live SQLite database as an assumed consistent backup. Shutdown allows 60 seconds; image updates retain the volume.

## Formula engine and external services

The bundled Pyodide engine runs Python formulas without requiring privileged Docker, gVisor kernel features or a GPU. Python packages/behavior can differ from a native Python installation. The container has a 3 GiB memory limit and two CPUs; large documents may require more resources. No model is loaded or changed, and AI assistant endpoints/credentials are not configured.

Telemetry, automatic version checks and the remote widget catalog are disabled. Built-in views remain available. Adding external widgets, webhooks or remote import URLs is an explicit user operation and may transmit data; none are provisioned here.

The official image has Linux amd64/arm64 variants for Docker on Windows, Linux and macOS. Definition/staging checks are separate from runtime support: image build, startup, document import, Pyodide formulas, API behavior and backup/restore still require runtime validation on each platform.

Upstream: https://github.com/gristlabs/grist-core/tree/v1.7.19
