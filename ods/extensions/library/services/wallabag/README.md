# Wallabag for ODS

Save selected web articles for later reading, tagging, annotation and export.
This MIT application is separate from a browser bookmark index: it extracts and
stores readable article content. Sites requiring authentication or using unusual
layouts may not extract successfully.

## Setup

Install `wallabag` from the library and provide:

- `WALLABAG_SECRET`: a persistent random 64-character hexadecimal signing secret.
- `WALLABAG_INITIAL_PASSWORD`: your initial administrator password, at least 12 bytes.
- `WALLABAG_INITIAL_EMAIL`: your administrator email address.

Open `http://localhost:11060` and sign in as `ods`. `WALLABAG_PORT` changes the
loopback port and the configured application URL together. Registration is closed;
email delivery is disabled, so email reset and email-based two-factor workflows
require additional SMTP configuration before use. Keep administrator credentials.

The image's installer normally creates wallabag/wallabag. A build-time patch
checks the exact 2.6.14 source patterns and replaces those initial credentials.
Startup validates configuration before invoking the upstream entrypoint. The
upstream installer runs only for an empty SQLite database; changing the initial
password setting does not reset an existing account. Manage later credentials
inside the application. Do not invoke the installer's reset option on saved data.

## Persistence and maintenance

`wallabag-data` holds the SQLite database; `wallabag-images` holds downloaded
images. Back up both volumes with the application stopped for a consistent copy.
Never remove these volumes when merely updating/recreating the container.
Database upgrades need the upstream documented `doctrine:migrations:migrate`
procedure after a backup; this recipe does not silently reset or migrate restores.

The pinned official image uses a root supervisor for setup and nginx/PHP-FPM
management, with application work under its upstream unprivileged identity. No
Docker socket, host filesystem, GPU, language model or shared database is mounted.
The official image publishes amd64, arm64 and ARM variants. Windows/macOS need
Linux containers in Docker Desktop; Linux uses Docker Engine. Actual platform
execution remains unverified.

Add URLs explicitly in Wallabag, or configure its own clients/API credentials.
Nothing imports Portal chats or subscribes to external feeds automatically.
Public routing, reverse proxies and asynchronous import workers are not configured.

## Validation boundary

Health checks read upstream `/api/info`. A successful response is server readiness,
not proof of authentication, extraction quality or restore integrity. Image build,
first install/login, article extraction and restart/restore remain runtime-pending.
No application containers were started for this integration.

Source: https://github.com/wallabag/wallabag/tree/2.6.14
Official container: https://github.com/wallabag/docker
