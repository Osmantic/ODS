# Healthchecks

Monitor cron jobs, backups and other scheduled project tasks by receiving start,
success and failure pings. This complements HTTP uptime monitoring: a reachable
server does not prove that its scheduled job ran. BSD-3-Clause upstream v4.4;
official image pinned by digest, amd64/arm64, CPU only.

## Installation and owner setup

Set independent random 64-hex `HEALTHCHECKS_SECRET_KEY` and
`HEALTHCHECKS_DB_PASSWORD` through the extension configuration. Keep both stable
across recreation. The signing secret protects sessions; changing the database
environment variable does not rotate an existing PostgreSQL password.

Open `http://localhost:11123` after activation. Registration is closed. Create the
owner with the upstream interactive command (it prompts for email and password):

```
docker exec -it ods-healthchecks ./manage.py createsuperuser
```

Run it once, then log in using those credentials. No default administrator or
password is supplied. A ready HTTP endpoint does not prove owner setup or alert
delivery is complete. Native uWSGI performs database migrations and starts alert
and report workers; no extra scheduler container is necessary.

## Connect actual project tasks

Create a check in the UI, choose its real expected period/cron expression and
grace time, and copy its private ping URL. Send `/start` when a job starts, the
plain URL only after success, and `/fail` on failure. Treat the check UUID/URL as a
secret; do not put it in public source. Do not send fabricated success pings.

Host-side Windows, macOS and Linux jobs use the localhost URL. An ODS container
on `ods-network` uses `http://healthchecks:8000/ping/<check-uuid>` instead: its
localhost points to itself. Other computers require deliberately configured
ingress and a matching public SITE_ROOT; this recipe exposes only host loopback.
It does not modify the host scheduler or connect existing projects automatically.

Configure notification channels in the application explicitly. SMTP is absent by
default, so email notifications/password-reset links cannot be assumed to work.
For email, configure the upstream EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS,
EMAIL_HOST_USER, EMAIL_HOST_PASSWORD and DEFAULT_FROM_EMAIL settings and verify
delivery. Integrations such as Gotify can be configured with their actual URL and
token. No recipients or outbound channels are provisioned by ODS. Native account
initialization may create upstream default records; review channels before use.

## Data and operation

The private PostgreSQL 17 companion has no published port. The named
`healthchecks-db-data` volume contains accounts, checks and ping history/body
data. Preserve a database dump and the signing secret for recovery. Do not delete
the volume during reinstall or change PostgreSQL major versions in place.
Back up before upgrading: native startup applies migrations automatically.

The application runs as upstream user `hc`, with a read-only root and writable
temporary directory. App and database each have a 1 GiB memory ceiling; app has
two workers/two CPUs. Native `/api/v3/status/` checks database connectivity, not
whether checks or notification delivery work. Ping bodies are capped at 10 KB;
avoid sending secrets in logs. The shared ODS network can reach the API.

Linux containers are required on Windows/macOS Docker Desktop and Linux Docker
Engine; no GPU/model selection or host-specific bind path is used. Image build,
login, heartbeat timing, notification delivery and restore remain runtime
validation pending. No containers or models were started for this addition.

Sources: [upstream](https://github.com/healthchecks/healthchecks/tree/v4.4),
[Docker setup](https://healthchecks.io/docs/self_hosted_docker/),
[ping API](https://healthchecks.io/docs/http_api/).
