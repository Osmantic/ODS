# Trino federated SQL

Optional Trino **483** provides a single-node SQL query engine over explicitly
configured catalogs. It ships a small generated TPC-H catalog for a first query.
Adding a connector lets an operator join approved local sources without copying
them into a new application database. Trino does not automatically discover or
authorize ODS databases.

## Enable with native HTTPS

Set these values in the Extensions form before enabling:

| Setting | Meaning |
| --- | --- |
| `TRINO_PASSWORD_HASH` | bcrypt hash for fixed user `ods`, cost at least 8 |
| `TRINO_INTERNAL_SECRET` | Random secret for native coordinator/worker authentication |
| `TRINO_TLS_PASSWORD` | Stable keystore password, at least 6 characters |
| `TRINO_HTTPS_PORT` | HTTPS host port, default `8448` |

Generate a hash with `htpasswd -nB -C 10 ods`; enter only the part after `ods:`
as `TRINO_PASSWORD_HASH`. Keep the original password in your password manager.
Use two additional independent random secrets, such as output from
`openssl rand -base64 32`, for internal authentication and the TLS keystore.

On first start, the native JDK `keytool` generates a local self-signed certificate
and private key in `data/trino/tls/server.p12`. It exports the public certificate
as `data/trino/tls/server.crt`. The certificate covers `localhost`, `127.0.0.1`,
and Docker service name `trino`, and expires after 365 days. This is a local
certificate, not a publicly trusted certificate or an automatic renewal service.

Clients must explicitly trust that public certificate obtained from the trusted
host filesystem. For example, `curl --cacert data/trino/tls/server.crt --user ods
https://localhost:8448/v1/statement --data 'SELECT count(*) FROM tpch.tiny.nation'`
prompts for the original password. A Trino client must follow the native response's
`nextUri` pages to obtain the final rows; the SQL result is 25. Configure your
Trino JDBC/Python/CLI client with this endpoint, username/password, and certificate
trust rather than disabling certificate verification.

The native query monitor UI is at `https://localhost:8448/ui/`, using the same
certificate and account. The ODS manifest deliberately has no HTTP browser link
for this HTTPS-only client endpoint. ODS checks the private HTTP `/v1/info`
listener on port 8080; the Docker healthcheck also requires `starting: false`.
HTTP SQL access is explicitly denied, including a forged forwarding header.
Do not enable `http-server.authentication.allow-insecure-over-http`: this Trino
option selects an unauthenticated identity mechanism, not password auth over HTTP.

## Add a local catalog

Installed recipe files live in `extensions/trino`. Add a reviewed
`catalog/<name>.properties`, following the connector documentation for version
483, then recreate through the existing extension lifecycle. For PostgreSQL:

```properties
connector.name=postgresql
connection-url=jdbc:postgresql://approved-source:5432/warehouse
connection-user=reports_reader
connection-password=${ENV:REPORTS_READER_PASSWORD}
```

The example's `REPORTS_READER_PASSWORD` must be explicitly passed to the Trino
container by an operator-managed Compose configuration; arbitrary host `.env`
entries are not automatically container variables. Alternatively, a private
catalog file may contain the source credential. Protect such files and backups;
catalog credentials grant every permitted Trino query that source account's
authority. Do not commit live credentials into the repository.

The bundled policy allows user `ods` to read configured catalogs and execute its
own queries, but denies user impersonation, writes, schema creation, and native
connector passthrough functions/procedures. The file access-control plugin's
default restriction to built-in functions/procedures is intentional. Also grant
the source account only the database operations it needs. A connector is an
operator-approved outbound network destination, not a sandbox for untrusted
catalog configuration. Trino does not proxy source credentials to arbitrary
clients.

Catalogs are static: adding a catalog takes effect on recreation. Dynamic
`CREATE CATALOG` and SQL-driven credential storage are not enabled. Changes to
the installed recipe should be backed up before a library reinstall replaces
recipe files. Reconcile operator-managed configuration against a new recipe
before upgrading; do not expect custom catalogs to survive an overwrite.

## Resources and state

Trino runs as UID/GID 1000 with no capabilities, a read-only root filesystem,
2 CPUs, 3 GiB container memory, and a 1.5 GiB Java heap. Query memory limits are
512 MiB / 768 MiB total, execution time 4 minutes, total run time 5 minutes,
and stage count 30. Temporary configuration, logs, and engine state use a
256 MiB tmpfs. These settings target small local queries; there are no claims
about concurrent-user throughput or large-data capacity.

Source data stays in its source system. Trino's query history and running work
are ephemeral and are lost on recreation. Persisted TLS identity lives in
`data/trino/tls`; configured catalogs and access rules live in the installed
recipe. There is no durable warehouse or saved-report database to back up here.
The single-node shared secret authenticates native internal requests; this
recipe does not configure additional workers or a distributed cluster.

## Rotation, backup, and rollback

To rotate the login password, generate a new hash, update `TRINO_PASSWORD_HASH`,
and recreate. Old credentials are rejected and the TLS identity remains stable.
Recreation interrupts running queries, so stop clients before planned changes.

Keep `TRINO_TLS_PASSWORD` with the private keystore backup. Changing it alone
does not re-encrypt an existing PKCS12 file; startup fails visibly. Restore the
matching password to recover. Renew or replace the certificate offline using
native `keytool`, preserving a backup and the desired DNS/IP names, then distribute
the new public certificate to clients through a trusted channel before resuming.
Do not silently delete a keystore after an error or weaken client verification.

For cold recovery, stop Trino, copy `data/trino/tls`, the installed recipe and
catalogs, and the matching ODS secret settings. Restore with UID/GID 1000
ownership and the same pinned image, then verify certificate identity, login,
and a known query against an available source. Back up source databases through
their own lifecycle. Disable stops the service and retains TLS files. Rollback
requires the matching configuration and cold TLS copy; it cannot restore
interrupted queries or undo changes made outside Trino.

## Qualification

```sh
python -m pip install pytest 'bcrypt==5.0.0'
python -m pytest tests/test_trino.py -q
sudo --preserve-env=PATH env ODS_TEST_TRINO=1 python -m pytest tests/test_trino.py -q -s
```

The opt-in fixture uses the pinned native Trino and PostgreSQL images in an
isolated network with random loopback ports. It verifies HTTPS with the generated
certificate, rejected credentials/HTTP bypass/impersonation, a Unicode JOIN
between PostgreSQL and TPC-H, denied mutation paths, recreation, password
rotation, and cold TLS identity restore. Source database state is inspected after
denied writes. Linux amd64 Docker on WSL2 is the qualified runtime; browser
rendering, ARM/native macOS/Windows, other connectors, certificate renewal,
distributed workers, HA, and load capacity require further validation.

References: [483 password authentication](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/password-file.md),
[HTTPS](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/tls.md),
[access rules](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/file-system-access-control.md),
and [PostgreSQL connector](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/connector/postgresql.md).
