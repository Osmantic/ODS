# SQLPage

Build database-backed web applications with SQL files that select native forms,
tables, charts and other components. MIT SQLPage v0.46.3, official digest-pinned
amd64/arm64 image. This is a programmable SQL application runtime, not a graphical
database editor or an AI model service.

## Local project workflow

Open `http://localhost:11126`. The initial page displays the actual tables/views
in the SQLite project database, initially empty. No sample business data is
inserted. The image's bundled example database is removed; an explicit persistent
database URL prevents fallback to an in-memory database.

Edit SQL pages in your project folder. Copy a selected page into the running
extension with Docker's cross-platform copy command, for example:

```
docker cp ./index.sql ods-sqlpage:/var/www/index.sql
docker exec -u 0 ods-sqlpage chown 1000:1000 /var/www/index.sql
```

Copy only reviewed project files, never a directory containing credentials.
Use SQLPage's parameter syntax for request inputs instead of string-concatenating
SQL. Page queries run with the application's database privileges. Refresh the
page after editing; migrations under `/etc/sqlpage/migrations` run on startup.
Restart deliberately after adding a migration and back up before schema changes.
The native startup and migration behavior is retained.

The `sqlpage-web` named volume contains `/var/www`; Docker populates the initial
SQL files only when the volume is first created. Recreating the container does
not overwrite your pages. `/ods-health.sql` is a small database-backed JSON probe;
preserve that reserved file when replacing project sources.

The `sqlpage-data` volume contains `/etc/sqlpage`, including `project.db` and
configuration/migrations. The explicit SQLite URL uses that location, independent
of the web root. Stop the service before taking a whole-volume backup, or use a
consistent SQLite backup that accounts for journal/WAL state. Back up source and
data together. No host bind directory or automatic Portal project sync is claimed.

## Access and configuration

There is no default authentication. Host HTTP is restricted to loopback, but ODS
containers on the shared network can reach port 8080. Configure native OIDC or
application authentication before making private data available to other users.
Do not publish the initial schema page as a production app. Shell execution via
`sqlpage.exec` is explicitly disabled; reviewed SQL can still read/write the
configured database and use other native SQLPage capabilities.

The minimal official image is used; no DuckDB/ODBC drivers or external database
are provisioned. To use PostgreSQL/MySQL or authentication, configure the actual
connection/provider deliberately according to upstream documentation. Do not
place credentials in publicly served files. Installation does not change any
existing database, model or project.

## Platform and limits

Linux containers on Docker Engine and Windows/macOS Docker Desktop; amd64 and
arm64, no GPU required. UID/GID 1000, read-only container root, writable source/data
volumes and bounded temporary storage. App limit 1 GiB/two CPUs. Native health
executes a small query; it does not validate your app's queries or authorization.

Image build, browser components, editing/reload, migrations and backup/restore
remain runtime validation pending. No service or model was started.

Sources: [source](https://github.com/sqlpage/SQLPage/tree/v0.46.3),
[configuration](https://github.com/sqlpage/SQLPage/blob/v0.46.3/configuration.md),
[tutorial](https://sql-page.com/your-first-sql-website/).
