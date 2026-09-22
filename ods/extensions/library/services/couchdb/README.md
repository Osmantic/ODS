# Apache CouchDB for ODS

A local JSON document database with revisions, indexes and replication APIs.
Fauxton provides its administration UI. This recipe creates a single-node server,
not a cluster or an automatic connection to other ODS applications.

## Setup

Set required `COUCHDB_PASSWORD` before enabling. Use a strong, single-line password
compatible with CouchDB's INI configuration. Open `http://localhost:11045/_utils/`
and authenticate as `ods`; the browser may first request HTTP credentials because
authentication is required except for the status endpoint.

The included `[couchdb] single_node=true` configuration provisions the system
databases on startup. Create your actual project database in Fauxton and configure
its users/security explicitly. Applications on `ods-network` can use
`http://couchdb:5984`; host clients use the published localhost port. Create an
appropriately scoped database user rather than giving every project the admin
password. No project database, replication target or remote account is seeded.

## Persistence and credentials

- `couchdb-data` retains databases, documents and indexes.
- `couchdb-config` retains local configuration, including the administrator hash
  and runtime settings. Back up both volumes; disabling must retain them.
- The environment creates the `ods` administrator only when it is absent.
  Changing `COUCHDB_PASSWORD` after initialization does **not** reset the persisted
  account. Use CouchDB's account/configuration tools for password changes and
  keep the ODS setup value synchronized for future restores.
- On a fresh volume, image configuration is copied in. Upgrades do not overwrite
  existing configuration; preserve UID/GID 5984 when restoring backups.

## Runtime

Official Apache-2.0 image **3.5.2.1**, pinned by digest for amd64/arm64. Windows and
macOS use Docker Linux containers; native Linux uses the same volumes and paths.
No GPU, model or context size is involved. The server runs under UID/GID 5984 and
no-new-privileges, with two CPUs and 2 GiB memory limits. Only HTTP is published,
on loopback; Erlang discovery/cluster ports are not published to the host.

The health probe requests the real `/_up` endpoint and rejects non-200 responses.
It uses bundled Bash because upstream removes curl from the final image. It
does not verify authentication, document writes, replication or restore behavior.
No host directory or Docker socket is mounted, and there is no automatic Portal
Playground association.

## Verification status

Image provenance/platforms, upstream initialization and configuration were
inspected. Schema, Compose resolution and ODS staging are separate checks.
**Build, login, document CRUD and restore runtime remain pending.** Local
acceptance should create a project database/document, restart, confirm revision
and document persistence, and verify an anonymous data request is refused.
No database or replication operation was performed while preparing this recipe.

- [Official container source](https://github.com/apache/couchdb-docker/tree/main/3.5.2.1)
- [Docker installation](https://docs.couchdb.org/en/stable/install/docker.html)
- [Single-node configuration](https://docs.couchdb.org/en/stable/setup/single-node.html)
- [Authentication configuration](https://docs.couchdb.org/en/stable/config/auth.html)
