# Dolt (Versioned Datasets)

Dolt **2.3.4** provides a MySQL-compatible SQL server whose tables can be
committed, branched, diffed, and queried at an earlier commit. Use it for local
evaluation examples, prompt datasets, or reproducible workflow inputs. ODS does
not move an existing application's database into Dolt or push data to DoltHub.

## Enable and connect

Set `DOLT_ROOT_PASSWORD` in **Extensions**, then enable Dolt. The standard
extension lifecycle copies the two configuration files and prepares
`data/dolt` and `data/dolt/.dolt` for UID 1000. Manual Compose users must do both
steps before starting the service.

| Setting | Default / meaning |
| --- | --- |
| `DOLT_SQL_PORT` | `3312`, host MySQL protocol port |
| `DOLT_ROOT_PASSWORD` | Required initial SQL superuser password |
| SQL user | `root`, for initial database and user administration |
| Container endpoint | `dolt:3306` on `ods-network` |

Connect with a MySQL-compatible client, for example:

```bash
mysql --host=127.0.0.1 --port=3312 --user=root --password
```

The password prompt avoids placing the credential in the command line. There
is **no browser UI**: the catalog intentionally has no HTTP launch link. Its
explicit startup check keeps it classified as a persistent service. Docker
health and dashboard checks use the native `/metrics` listener on internal port
11112; that port is not host-published. Metrics are unauthenticated to trusted
Docker peers and can include database names. A healthy process does not prove
that a particular SQL user can query its database.

Only the SQL port is host-published, on loopback by default. `BIND_ADDRESS` and
`DOLT_SQL_PORT` overrides are preserved. The recipe does not provision TLS;
configure encrypted transport before sending SQL data beyond a trusted local
connection. Remote clone/push and MCP listeners are not enabled.

## Version an evaluation dataset

After connecting as an administrator:

```sql
CREATE DATABASE evaluation;
USE evaluation;
CREATE TABLE examples (id INT PRIMARY KEY, prompt TEXT);
INSERT INTO examples VALUES (1, 'Summarize the supplied document');
CALL DOLT_COMMIT('-Am', 'Baseline examples', '--author', 'Dataset Owner <owner@example.org>');
CALL DOLT_CHECKOUT('-b', 'candidate');
UPDATE examples SET prompt = 'Summarize the supplied document in three sentences' WHERE id = 1;
CALL DOLT_COMMIT('-Am', 'Candidate prompts', '--author', 'Dataset Owner <owner@example.org>');
SELECT * FROM dolt_log;
CALL DOLT_CHECKOUT('main');
```

SQL `COMMIT`/autocommit persists a transaction; it does **not** create a Dolt
history commit in this recipe. Call `DOLT_COMMIT` for meaningful dataset
versions. `DOLT_CHECKOUT` changes the current SQL session's branch; new sessions
use their selected/default branch. Historical reads use `SELECT ... AS OF
'commit-hash'`. Record the exact hash with an evaluation result rather than a
moving branch name. Replace the example author with the actual dataset owner;
the mounted default identity `ODS <ods@localhost>` is only an initialization
identity, not evidence of who reviewed data.

Create database-scoped SQL users with `CREATE USER` and `GRANT`, then give those
credentials to workflows. For example, grant only `SELECT ON evaluation.*` to
an evaluation reader. Keep the root credential for administration. Native SQL
grants and Dolt branch permissions remain owned by Dolt, not by the dashboard.

## Persistence, credentials, and recovery

The native `dolt sql-server` initializes its required root password before
accepting connections. It reads the bootstrap environment only when no
privileges database exists. `ALTER USER` rotates a password immediately;
recreating the container with the old bootstrap value does not undo rotation.
Verify the replacement credential, update clients and the recorded ODS secret,
and confirm that the old credential is rejected. A Docker/host administrator
can use Dolt's local recovery access; SQL authentication is not isolation from
the owner of the data directory.

Keep all of `data/dolt`, including hidden directories: database histories,
`.doltcfg/privileges.db`, branch-control state, and native configuration are
part of the recovery unit. The recipe mounts its default global identity and
telemetry setting from `config/dolt-global.json` read-only. Dolt telemetry is
disabled; the private health metrics listener remains enabled. Native server
configuration is in `config/dolt-server.yaml`.

For a same-version cold backup, stop Dolt and copy the entire data directory
plus both ODS config files to a private location. Restore while stopped,
preserve UID/GID 1000 ownership for both data mounts, and restart the same
pinned image. Verify a known historical query and a restricted account before
resuming writers. Disabling the extension retains data. Do not delete the
original during recovery or copy only the visible database directories.

The recipe caps the container at 2 CPUs/2 GiB and 25 SQL connections; the Go
memory target is soft. Large histories, queries, or concurrent writes require
capacity planning. Take a backup before an image upgrade and restore the
pre-upgrade backup for rollback; do not assume older binaries can read newer
storage formats. Linux amd64 qualification covers native SQL authentication,
Unicode rows, branch/history isolation, restricted writes, credential rotation,
recreation, and cold restore. It does not qualify MySQL drop-in compatibility,
replication, cross-version restores, ARM hardware, or large datasets.
