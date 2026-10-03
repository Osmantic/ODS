# Datasette

Explore selected SQLite snapshots with a browser, read-only SQL, faceted tables and
native JSON/CSV endpoints. Apache-2.0 Datasette 0.65.5 with exact Python dependencies
and a pinned Python 3.13 base. This is a data explorer, not a database administrator
or a general database server.

## Import project data

The empty installation shows the native Datasette index without sample datasets.
Create a consistent SQLite snapshot with the source application's export function
or SQLite backup API. Do not copy an active database file without its WAL state.
Import only data intended for everyone who can access this local service:

```sh
docker cp ./project-snapshot.db ods-datasette:/data/project-snapshot.db
docker exec --user 0 ods-datasette chmod 0444 /data/project-snapshot.db
docker restart ods-datasette
```

Open `http://localhost:11117`. Startup discovers top-level `.db`, `.sqlite` and
`.sqlite3` files, rejects non-SQLite files, symlinks and duplicate database names,
and opens each through Datasette's native immutable mode. Use unique filename stems.
After replacing or adding a snapshot, restart; never modify an immutable snapshot
while the reader is running. Stop the reader before replacing existing snapshots.

The SQL interface has a one-second query time limit and a 1,000-row response limit.
Full database downloads and unbounded CSV streaming are disabled. Normal paginated
queries can still expose every selected row: these limits are not data access controls.
No arbitrary SQLite extension, plugin, remote database, model or host directory is
loaded automatically. CSV must first be converted to SQLite using a chosen project
tool; a built-in CSV upload UI is not claimed.

## Persistence and access

`datasette-databases` stores imported snapshots independently of container rebuilds.
Back it up with the service stopped. Original project databases are never mounted
or edited by this recipe. Portal file import/project association remains pending.
The image runs as UID/GID 1000 with a read-only root, writable import volume, bounded
temporary space and 1 GB memory limit.

Host port 11117 is loopback-only. Datasette has no login configured; other containers
on `ods-network` can read imported data too. Authentication must be deliberately added
before exposing confidential datasets or remote access. This installation does not
issue a root login token or install an authentication plugin silently.

## Compatibility and verification

Python-based amd64/arm64 Docker builds target supported Linux, Windows and macOS
hosts without a GPU. The native versions endpoint checks HTTP availability, not
dataset correctness. Schema/dependency checks do not establish runtime compatibility:
image build, snapshot import, SQL/API behavior and restore remain pending; no service
or model was started while preparing this recipe.

Sources: https://docs.datasette.io/en/stable/installation.html,
https://docs.datasette.io/en/stable/authentication.html and
https://github.com/simonw/datasette.
