# Neo4j Bolt graph store

Optional Neo4j Community 2026.08.1 for applications that require Neo4j's Bolt
driver protocol and Cypher behavior. It includes Browser and the HTTP Query API.
It does not automatically build a knowledge graph or send documents to a model.
FalkorDB and SurrealDB integrations use different client protocols; their data
and credentials are not migrated by enabling this service.

## Connect

Set a strong initial `NEO4J_PASSWORD` of at least 12 characters, without slash
or line break. Enable the extension and open Browser, by default
`http://localhost:7474/browser/`. Sign in as `neo4j`; the local Bolt address is
`bolt://localhost:7687`. Custom HTTP/Bolt ports also update advertised endpoints.

Applications on `ods-network` should explicitly use the direct driver URI
`bolt://neo4j:7687` with their configured credentials. Using a routing URI such
as `neo4j://...` can return the external advertised address; do not assume that
address is reachable inside a container. Remote access requires deliberate
`NEO4J_PUBLIC_HOST`, binding, TLS and access-control configuration. Both published
ports bind to loopback by default.

The installer copies the password guard through the existing host-agent config
sync into config/neo4j/entrypoint.sh before startup. Compose mounts that file
read-only and refuses to create a directory in its place. Include config/neo4j
in deployment backups; retain the reviewed guard alongside the matching image
when rolling back. It contains no deployment password.

For an explicit HTTP integration, POST authenticated JSON to
`/db/neo4j/query/v2` with `statement` and `parameters`. Always inspect the
response's error fields: HTTP 202 alone does not mean the Cypher query succeeded.
Use parameters for application data rather than concatenating query strings.

## Data and credentials

The complete database and authentication state live in `ods/data/neo4j/data/`;
logs live in `ods/data/neo4j/logs/`. The image's normal entrypoint prepares
permissions and drops to its Neo4j user. Its writable configuration/startup
layout is preserved. No Enterprise license acceptance, extra plugin, external
database, Docker socket or host-root mount is introduced.

The initial password variable applies to a fresh database. It does not change
an existing account password. Rotate credentials through Neo4j account
management, update clients, and keep the deployment's initial-password setting
consistent for future recovery. The wrapper rejects unsupported initial-password
syntax without echoing the secret.

Stop the service before copying the complete data directory and private
deployment configuration for a backup. Test the copy with the same pinned
image in an isolated deployment before relying on it. Do not copy active store
files as a consistent backup. Follow Neo4j's offline dump/load workflow when
moving between versions. Rollback requires the old image and a compatible
pre-upgrade backup, not simply changing an image tag against a migrated store.

## Operating limits

The profile allocates a 512 MiB heap, 512 MiB page cache, 2 CPUs and 2 GiB
container memory. Size these deliberately for larger graphs and monitor disk,
transaction logs and query load. Community is a single-server profile, not
a clustered or tenant-isolated deployment.

Server usage reporting, client product analytics, Fleet Manager and fleet
discovery are disabled. These settings are not a network firewall; an authorized
query or an explicitly configured integration can still perform network I/O.
The public discovery endpoint used for health proves the HTTP listener is up,
not that a write, transaction or recovery operation succeeded.

## Validation

`pytest ods/tests/test_neo4j.py -q` checks the rendered installation, credentials
and published/advertised endpoints. The opt-in `ODS_TEST_NEO4J=1` run adds actual
HTTP Query API and Bolt operations, authentication rejection and a recovery
drill with synthetic graph data and uniquely owned Docker resources.

Sources: [Docker operation](https://neo4j.com/docs/operations-manual/current/docker/introduction/),
[Query API](https://neo4j.com/docs/query-api/current/query/),
[configuration](https://neo4j.com/docs/operations-manual/current/configuration/configuration-settings/).
