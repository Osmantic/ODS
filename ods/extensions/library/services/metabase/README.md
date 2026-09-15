# Metabase (Local SQL Analytics)

Optional Metabase Open Source **0.63.17** for questions and reports over local
SQL data sources. A private PostgreSQL **17.11** application database retains
accounts, saved questions, permissions, and connection details. It is separate
from the databases you analyze; ODS does not automatically connect another
service's database or grant Metabase access to it.

## Enable and complete setup

In **Extensions**, set two separate random secrets, then enable Metabase:

| Setting | Default / meaning |
| --- | --- |
| `METABASE_DB_PASSWORD` | Required initial password for private application PostgreSQL |
| `METABASE_ENCRYPTION_KEY` | Required stable key, at least 16 characters; `openssl rand -base64 32` is suitable |
| `METABASE_PORT` | `3032`, host UI/API port |
| `METABASE_SITE_URL` | Optional; empty derives `http://localhost` with the selected host port |

Open `http://localhost:3032` and immediately complete the native first-user
setup. That user becomes the Metabase administrator. **Neither ODS secret is
the UI login password.** Until setup completes, anyone who can reach the UI
can claim the first administrator; keep the default loopback publication and
trusted Docker peers during onboarding. The setup token is consumed when that
account is created and does not return on normal restart.

The application has its own authenticated sessions. Create additional users,
groups, and permissions in Metabase. Public sharing, anonymous usage tracking,
automatic update checks, and automatic sample-content creation are disabled by
this recipe. Sharing the native database connection or enabling more features
is an explicit operator decision. No email or external analytics destination is
configured by ODS.

The host UI/API listener follows `BIND_ADDRESS`. If using a reverse proxy, set
the full HTTPS `METABASE_SITE_URL`; it takes precedence over the local port
default. A site URL does not configure TLS or a proxy by itself. The companion
PostgreSQL port is not published and its network is private to this extension.

## Connect a data source and save a question

Use **Admin settings → Databases** to add a supported local database, with a
dedicated account limited to the tables and read operations the report needs.
For another container on `ods-network`, use its service DNS name and internal
port rather than `localhost`. Do not connect the private Metabase application
database as an analytics source or reuse its administrative database credential.

Create a SQL question, run it, and save it for later use. Metabase enforces its
own user/group permissions, and the source database's grants enforce the final
query authority. The native regression uses an independently created fixture
database and a SELECT-only role, verifies real SQL results, and confirms that a
write request fails without changing the rows. Production schemas, roles, and
data-source backups remain owned by their operators.

## Secrets and persistence

`METABASE_ENCRYPTION_KEY` protects saved connection details in the application
database. Retain it privately with backups. Changing or losing it is not normal
password rotation: this version refuses to start with a mismatched key. Restore
the correct key, or follow the version's offline encryption-key migration
procedure after backing up; never regenerate the key on each install/restart.
This encryption does not encrypt all reports, query results, or the entire
PostgreSQL volume.

`METABASE_DB_PASSWORD` is PostgreSQL's initial credential. An initialized
PostgreSQL data directory ignores changes to `POSTGRES_PASSWORD`. Rotate the
database role through PostgreSQL and update the application's matching secret
before recreating the service. Change UI passwords through Metabase's account
controls. These are three different credential lifecycles.

All application state lives in `data/metabase/postgres`. Standard extension
activation prepares it for PostgreSQL UID 70. The Metabase process runs as UID
2000 with a read-only root filesystem; bundled drivers extract into temporary
storage and are recreated at startup. Manually starting the fragment requires
preparing the PostgreSQL data directory ownership first. Disabling retains it.

## Capacity and recovery

Metabase is capped at 2 CPUs/2 GiB with a 1 GiB Java heap and a 512 MiB temporary
filesystem. Its private database has a separate 1 CPU/512 MiB limit. Fresh
database migrations and driver loading can take several minutes. `/api/health`
reports application availability; it does not validate each connected source.
Large scans, many users, and heavy queries need additional capacity planning.

For same-version cold recovery, stop **both** containers, copy the complete
`data/metabase/postgres` directory, and retain the two ODS secrets and any custom
configuration. Restore while stopped with UID/GID 70 ownership, start the same
pinned images, then verify login and a known saved question against an available
source database before resuming users. Back up source databases separately;
the Metabase application backup does not contain their data. Do not overwrite a
live PostgreSQL directory or discard the original while testing recovery.

Back up before an image upgrade. Metabase performs application-database
migrations, so rollback uses the matching pre-upgrade database and image.
Linux amd64 qualification covers native setup/session boundaries, real
PostgreSQL queries, saved state, encrypted connection details, disabled public
links, read-only source permissions, recreation, cold restore, and recovery from
a wrong encryption key. Browser rendering, ARM/native macOS/Windows, SMTP,
other database drivers, cross-version restores, and large-data performance are
not qualified by this fixture.
