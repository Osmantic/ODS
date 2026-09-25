# Restic REST Server for ODS

BSD-2-Clause Rest Server 0.14.0, using the official digest-pinned static binary
in a pinned Alpine runtime. This is a backup destination, complementary to the
Backrest client/UI; it is not another copy of Backrest or a backup scheduler.

## Setup and project use

Set `REST_SERVER_PASSWORD` to a random 64-character lowercase hexadecimal
secret. Install `rest-server`. It listens on loopback port 11144, configurable
through `REST_SERVER_PORT`, without a browser UI. The transport user is `ods`.

Configure a Restic client with repository `rest:http://localhost:11144/ods/PROJECT`
and supply `RESTIC_REST_USERNAME=ods` and `RESTIC_REST_PASSWORD` through the
client's credential configuration. The repository encryption password is a
separate client-owned secret (`RESTIC_PASSWORD` or its password-file mechanism).
Keep that key outside this server; losing it makes the encrypted backup unusable.
Initialize the repository with the client before the first backup.

For Backrest or another authorized container on `ods-network`, use
`rest:http://rest-server:8000/ods/PROJECT`, with the same separate transport and
repository credentials. Do not use container-local localhost for another
service. Project paths, source mounts, schedules and retention are owner choices;
none are silently created or associated with the current Portal project.

## Persistence and retention

`rest-server-data` stores the actual repository objects under `/data`. Keep the
volume across upgrades and include it in the owner's backup plan. Authentication
is bcrypt-based and regenerated from configuration at startup; changing the
transport password requires updating clients but does not re-encrypt repositories.

Append-only mode prevents overwriting/deleting existing backup objects, except
native lock handling. Normal backup and restore are supported; client-side
forget/prune needs a separately controlled maintenance procedure, for example
stopping the server and using a trusted local Restic client against its volume.
This recipe does not schedule destructive retention or turn append-only off.
Monitor storage growth: no arbitrary repository quota is imposed.

Private-repository mode confines the `ods` account to `/ods`. Multiple project
subdirectories under that account are organizational, not separate access-control
boundaries. Do not claim per-project isolation from a shared account.

## Runtime

CPU-only amd64/arm64 Linux containers on Linux Docker Engine or Windows/macOS
Docker Desktop. Nonroot UID1000, read-only root, private temporary credentials,
512 MB RAM and two CPU limits. No GPU or loaded model is required or changed.
HTTP is local-only; remote transport requires an explicitly configured TLS
deployment. Encryption at the client does not protect HTTP authentication secrets.

The authenticated health probe reads a repository config path and accepts 404
before initialization; it does not write a sample repository or verify backup
integrity. An unauthorized response fails health. Use Restic check/restore for
actual data verification. The ODS launch action is intentionally absent.

Schema, staging and Compose checks do not prove a backup cycle. Build, client
authentication, init/backup/restore, append-only enforcement and platform runtime
validation remain pending; no containers or models were started during packaging.
