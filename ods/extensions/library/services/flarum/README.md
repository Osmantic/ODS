# Flarum

Discussion forum with topic tags, community accounts, permissions and moderation. This is a distinct forum service, not another chat frontend for the loaded model.

## Setup

Supply FLARUM_DB_PASSWORD as a random 64-character hexadecimal secret. This restricted format also keeps upstream shell/YAML/PHP configuration interpolation well-defined. Retain it with backups; changing it in Compose does not rotate credentials in an existing MariaDB database.

Open http://localhost:11133 and sign in with the native initial flarum / flarum account. Immediately change the password and replace its placeholder email through account settings. Review registration and posting permissions before inviting users. ODS does not consider owner setup finished simply because the login page responds. Host publishing is loopback-only; other ods-network containers can reach flarum:8000. No SMTP is configured, so password-reset and verification email require configuring a real mail server in administration. No sample discussion, external identity provider or model account is created.

The canonical URL uses localhost and FLARUM_PORT. Remote access requires deliberately changing FLARUM_BASE_URL and configuring ingress/TLS; another host cannot use this localhost URL.

## Storage and scheduler

flarum-data retains native assets, storage and extension declarations under /data. flarum-db-data retains private MariaDB 11.4.13 data. Back up both stopped volumes together with configuration and the database password. The database has no published host port and joins only the private network.

Native S6 startup begins as root for filesystem preparation, then runs application processes as UID/GID 1000. Keep /init and a writable root filesystem. The scheduler sidecar starts after the application health check and runs the native Flarum scheduler each minute. It has no host port and shares application data and database configuration. The application has two CPUs/1 GiB; the database two CPUs/1 GiB and scheduler one CPU/512 MiB.

Flarum extensions are separate from ODS extensions. This image retains their Composer requirements and can reinstall them on startup; only add explicitly selected compatible packages, with version constraints and backup first. No third-party Flarum add-ons are installed by this recipe. The private scheduler network intentionally provides no general Internet access.

The HTTP probe establishes forum response availability; MariaDB uses its native connection/InnoDB health probe. Neither verifies posting, moderation or email delivery. Portal project association and API credentials are not automatically configured.

## Provenance and verification

MIT Flarum 1.8.19 through the community-maintained CrazyMax image, pinned by digest and image source commit; MariaDB is separately pinned. Linux amd64/arm64 images support the same named-volume layout under Linux Docker and Windows/macOS Linux-container runtimes, without GPU/model coupling.

Build, installation, first login/password change, posting, scheduler, mail configuration, backup/restore and host-platform execution remain unverified. No container or model was started.

Sources: [Flarum](https://github.com/flarum/framework), [image source](https://github.com/crazy-max/docker-flarum/tree/044c3e966b04b001743c3fe8cca4026f6e7bdab2).
