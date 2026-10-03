# pgvector — PostgreSQL vector search for projects

Official pgvector 0.8.6 on PostgreSQL 17 Bookworm, pinned by multiarch image digest. PostgreSQL license. Upstream: https://github.com/pgvector/pgvector/tree/v0.8.6.

This extension provides relational storage plus vector types, distance operators and indexes. It does not generate embeddings, select a language model or automatically ingest Portal conversations. The project supplies vectors with dimensions matching its chosen embedding model.

## Credentials and connection

Configure `PGVECTOR_ADMIN_PASSWORD` and `PGVECTOR_APP_PASSWORD` as two distinct random 64-character hexadecimal secrets. The initialization creates database `vectors`, installs `vector`, and creates the restricted `ods_app` login owning schema `project`. Use this application account in projects; the `ods_admin` bootstrap account is a PostgreSQL superuser and is for administration only.

Host clients: `localhost:11098`; other ODS containers: `pgvector:5432`. Database `vectors`, username `ods_app`. Supply the password through the client's secret mechanism, not a committed connection string. Loopback is the only published listener. No HTTP page is provided; connect with a PostgreSQL client or explicitly configured database UI.

An application can create `project.documents (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, embedding vector(384))` and query cosine similarity using `embedding <=> $1`. Choose dimensions from the actual embedding model, not this example. Tables, indexes and documents are never seeded automatically. The shared application account is not tenant isolation; distinct projects needing isolation require separate roles/schemas and grants provisioned by the administrator.

## Persistence and upgrades

`pgvector-data` persists PostgreSQL's `/var/lib/postgresql/data`. Initialization SQL and both passwords apply only on the first empty volume. Later environment edits do not rotate existing database passwords: change the role password through an authenticated administrator connection, then update configured clients/health secret. Never delete the volume to resolve a credential mismatch.

Back up with PostgreSQL-aware tools (`pg_dump` plus roles, or consistent physical backups). Do not copy a running data directory as an ordinary folder backup. A PostgreSQL major-version upgrade requires a supported dump/restore or pg_upgrade procedure, not just changing the image tag. Removing the extension does not authorize erasing its data.

Upstream startup is retained to initialize/chown the volume and drop to the postgres user. Host SQL uses SCRAM authentication; no trust override. Resources: 1 GiB, two CPUs, 128 MiB shared buffers, 50 connections and 128 MiB shared memory. Index builds may require deliberate resource adjustments for larger datasets.

The native health probe authenticates over TCP as `ods_app`, checks extension version and evaluates a vector distance without creating rows. Failed credentials or missing initialization therefore cannot report healthy solely because a port is open.

## Compatibility and validation

Official image includes Linux amd64 and arm64. Runs through Docker Engine/Desktop on Windows, Linux and macOS; no GPU or inference dependency. Configuration/schema checks do not prove deployment: image build, initialization, actual SQL persistence, restore and host runtime validation remain pending. No database or model was started during recipe preparation.
