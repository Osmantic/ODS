# Neo4j Community

Graph database for connected project data, queried using Cypher. The Community edition is used explicitly; no Enterprise license agreement, paid graph-data-science feature or external model provider is configured.

## Initial use

Set NEO4J_PASSWORD to a random 64-hex secret. Native first initialization creates credentials for username neo4j. Open http://localhost:11139/browser/ and connect to bolt://localhost:11140 with those credentials. A later change to NEO4J_PASSWORD does not reset authentication stored in an existing database. Use the native password change procedure and retain the updated credentials with project configuration.

The interface port is NEO4J_PORT (11139) and the Bolt driver port is NEO4J_BOLT_PORT (11140). Both bind only to loopback, and native advertised addresses match those host ports. HTTPS is not published. Remote use requires deliberate TLS and reachable advertised-address configuration.

A project container on ods-network can use bolt://neo4j:7687 directly. Use a direct Bolt connection for that path: the localhost routing address advertised for host clients is not a usable container address. Other host/remote drivers must use an endpoint reachable from their own execution environment.

Installing the service does not create a sample graph, import files, grant Portal access, install APOC/GenAI plugins or generate embeddings. Configure the project's schema, import and credentials explicitly. Community capabilities and permissions differ from Enterprise; do not assume per-project isolation from naming conventions.

## Persistence and resources

neo4j-data retains /data, including database contents, transactions and authentication. neo4j-logs retains /logs. Back up the stopped data volume and configuration, or use the native Community dump/load workflow appropriate to this version. Recreating the container retains data; removing the volume does not. Test upgrades against a backup before changing database versions.

The official entrypoint and tini process supervision are preserved, running as UID/GID 7474. The container filesystem remains writable because native startup generates configuration and runtime files under NEO4J_HOME. No host directory, Docker socket or import directory is mounted. Optional plugin auto-download is explicitly empty.

Two CPUs and 3 GiB memory bound the container. Initial heap is 512 MiB, maximum heap 1 GiB and page cache 512 MiB, leaving room for JVM/native memory. Large graphs or vector indexes require additional capacity and tuning. GPU/model configuration is unchanged.

Root HTTP health checks server response only; it does not verify successful Bolt authentication, graph queries or restore integrity. The browser performs its own authenticated database connection.

## Provenance and verification

GPL-3.0 Neo4j Community 2026.08.1 in the digest-pinned official image; image metadata identifies the Community tarball. amd64/arm64 manifests available. Linux Docker and Windows/macOS Linux-container runtimes use named volumes and the same internal paths; client addresses still depend on where the client runs.

Build, initial password/login, Bolt queries, import, password rotation and restore/platform runtime remain unverified. No containers, queries, browsers or models were started.

Sources: [Docker setup and initial authentication](https://neo4j.com/docs/operations-manual/current/docker/introduction/), [source license](https://github.com/neo4j/neo4j/blob/2026.08/LICENSE.txt), [pinned image packaging](https://github.com/neo4j/docker-neo4j-publish/tree/01da8618a685a3af37b1cb4606ee2396c9fb397d/2026.08.1/trixie/community).
