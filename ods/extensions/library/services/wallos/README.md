# Wallos

Track subscription amounts, renewals, categories and recurring expenses in the native Wallos application. This is an organizational tool, not a bank connection, payment processor or automatic cancellation service.

## Setup and schedule

Open `http://localhost:11094` and complete the native initial account setup. No default user or subscription data is seeded by ODS. Review registration/access settings before sharing the instance. Host publishing is loopback-only, while ODS-network containers can reach `wallos:80`.

Set `WALLOS_TIMEZONE` to the intended IANA timezone, such as `America/Sao_Paulo`, before configuring reminders. Default is `Etc/UTC`. The upstream startup and cron scheduler are retained for migration, next-payment updates and configured notification jobs. A healthy web page does not prove a reminder was delivered.

Notification destinations, SMTP, OIDC, exchange-rate API keys and AI providers are not configured. No messages are sent on your behalf by installation. Wallos may check GitHub for updates at startup; exchange-rate updates and logo search can use upstream external services. This recipe is not advertised as network-isolated. Configure these features deliberately before entering data that should not leave the local system.

## Storage and ownership

`wallos-db` persists `/var/www/html/db`, including SQLite and native configuration. `wallos-logos` persists uploaded logos and avatars. Back up both with the service stopped; copying only the database omits images. The startup script handles database creation/migrations and volume ownership using upstream `www-data` UID/GID 82.

The official startup needs root to adjust the PHP worker identity, own files and launch cron/nginx/PHP-FPM; this recipe retains that process model instead of forcing a UID that breaks it. It does not mount the Docker socket, host files or devices. Memory is limited to 1 GiB and CPU to one core, with bounded Docker logs. No GPU or ODS model selection is involved.

Use native import/export or a deliberately configured API client to associate data with a project. Installing Wallos does not scan emails, import financial files, connect accounts or create a Portal project association.

## Readiness and verification

The upstream `/health.php` only returns OK. ODS instead probes `/ods-health.php`, which opens the existing SQLite file in read-only mode and checks that application tables exist. Missing/unreadable/empty databases return 503 without internal details. This establishes PHP/database availability, not every migration, login or scheduler outcome.

GPL-3.0 version 5.8.1, released 2026-09-18, official image digest pinned for amd64, arm64 and ARM Linux containers. Windows/macOS require a Linux-container Docker runtime. Native initialization, PHP probe execution, login, record editing, scheduler delivery and backup/restore/platform execution remain runtime-pending. No service or model was started.

Sources: [versioned upstream](https://github.com/ellite/Wallos/tree/v5.8.1), [native startup](https://github.com/ellite/Wallos/blob/v5.8.1/startup.sh).
