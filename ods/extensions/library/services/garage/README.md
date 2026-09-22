# Garage S3

S3-compatible storage for project artifacts and application uploads. Official
AGPLv3 Garage 2.3.0 static binary, copied into a digest-pinned Alpine image for
startup validation and health probing. No model or GPU is required.

## Initialize and connect a project

Set `GARAGE_RPC_SECRET` to a stable, random 64-character hexadecimal secret before
installation. Native `server --single-node` initializes the local layout. It does
not create a bucket, S3 key or public access rule. After installation, create a
dedicated project bucket and key through the native CLI, for example:

```sh
docker exec ods-garage garage bucket create my-project
docker exec ods-garage garage key create my-project-key
docker exec ods-garage garage bucket allow --read --write --key my-project-key my-project
```

The key command returns sensitive access credentials: store them in the project's
secret configuration, not source control or chat logs. Use endpoint
`http://localhost:11116`, region `garage`, and path-style addressing in the S3 client.
Within `ods-network`, the endpoint is `http://garage:3900`. Grant only the permissions
needed for each project. This recipe never connects a project or changes an existing
application's S3 configuration automatically. Portal project association is pending.

This is an API extension without a bundled browser console; its launch action does
not pretend the S3 root is a web application. Garage implements a documented subset
of S3, not every AWS API; check compatibility before selecting it for an application.
Website hosting, public buckets, remote cluster discovery and metrics export are
not enabled. The RPC secret is a node credential, not an S3 access key.

## Persistence and boundaries

`garage-meta` stores SQLite, node identity and layout; `garage-data` stores object
blocks. Back up both together while stopped and preserve the RPC secret separately.
Never restore only object blocks or reset metadata to recover a node. Follow the
upstream upgrade procedure before changing versions. This single-node local setup
has no replication redundancy and is not a production distributed cluster.

UID/GID 1000, read-only container root, 1 GB memory limit and two named volumes avoid
host directory assumptions. Storage capacity is not capped by the memory limit;
monitor disk usage and set bucket quotas as required. S3 binds only to host loopback,
while RPC/admin bind to the container's loopback and have no host port mappings.
Other containers on the ODS network can reach S3 and still need a permitted key.
Remote access requires an explicit TLS/network design.

## Verification

The upstream image contains amd64/arm64 binaries for supported Docker environments
on Windows, Linux and macOS. Native `/health` reports node quorum availability.
It does not verify S3 credentials, upload/download, backups or application compatibility.
Image build, runtime initialization, S3 operations and restore remain unverified;
no service or model was started while preparing this integration.

Sources: https://garagehq.deuxfleurs.fr/documentation/quick-start/,
https://garagehq.deuxfleurs.fr/documentation/reference-manual/admin-api/,
https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/ and
https://git.deuxfleurs.fr/Deuxfleurs/garage/src/tag/v2.3.0.
