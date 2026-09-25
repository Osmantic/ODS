# Umami for ODS

Self-hosted website traffic and event analytics with a PostgreSQL database.
Installation creates the analytics service, not a tracking deployment on any
existing website. No example events or fictional visitor counts are seeded.

## Setup and tracking

Configure the four required secrets before enabling:

- `UMAMI_DATABASE_PASSWORD`: password for the dedicated PostgreSQL service.
- `UMAMI_APP_SECRET`: random authentication secret, at least 32 characters.
- `UMAMI_TWO_FACTOR_KEY`: exactly 64 hexadecimal characters; preserve it for
  users who enable two-factor authentication.
- `UMAMI_INITIAL_PASSWORD`: 12–72 UTF-8 bytes for the initial `admin` account.

Open `http://localhost:11056`, log in as `admin` with your initial password,
register the actual website in Settings and copy the tracking snippet generated
for it. Add that snippet to the selected project's layout only when you intend
to collect visits. Check that the script URL is reachable from visitors' browsers.
Their `localhost` is not your ODS computer, so publicly hosted websites need an
explicitly configured, reachable HTTPS analytics endpoint.

No project files, model settings, browser extensions or website scripts are
modified by installing this recipe. Collection requests can be blocked by browser
settings or site policy; an empty dashboard is not proof that a site has no users.

## Account initialization and startup

Upstream migrations create an account with a known default hash. The ODS startup
runs those migrations, conditionally replaces that exact account/hash using your
initial password, then updates the tracker and starts the HTTP server. It does
not reset an account whose password has already changed. Changing the environment
variable later is therefore not a password-reset mechanism. A restored database
still using the exact upstream default is upgraded by this same condition.

SQL values are parameterized and the update repeats the original-hash condition
to avoid overwriting a concurrent password change. Initialization errors prevent
HTTP startup. This bootstrap uses the versioned Prisma client and password
algorithm from the official image; its imports are checked during the image
build. Database passwords are URL-encoded, including literal percent signs, so
special characters cannot change the connection destination.

## Persistence and runtime

`umami-db-data` retains accounts, websites, events and analytics in PostgreSQL 16.
The database has no published host port. Keep its volume and all authentication/
encryption secrets when disabling or restoring the extension. Use a consistent
PostgreSQL backup or stop services before a full volume backup. Preserve backups
before migrations; an image downgrade is not a database rollback. The application
container is replaceable and has no invented persistent data volume.

Official MIT **3.4.0** image pinned for Linux amd64/arm64. Windows and macOS use
Docker's Linux engine. The app retains upstream UID 1001, gets two CPUs and 2 GiB
RAM, and uses Docker init; PostgreSQL has a 1 GiB limit. No GPU, AI model or context
configuration is changed. Telemetry/update checks are disabled. Redis, ClickHouse,
Kafka and an external identity provider are not required or implicitly installed.

The native `/api/heartbeat` endpoint is checked with curl configured to reject
HTTP errors. This checks application availability, not successful collection.
**Image build, migrations, account initialization, login, event ingestion, reports
and restore remain runtime-unverified.** Unit tests cover secret validation and
database URL encoding; schema/Compose/staging checks are separate from those
runtime behaviors. No tracking, containers or model inference were started.

- [Versioned source](https://github.com/umami-software/umami/tree/v3.4.0)
- [Installation](https://docs.umami.is/docs/install)
- [Configuration](https://docs.umami.is/docs/environment-variables)
