# Apache Solr for ODS

Apache-2.0 Solr 10.0.0, official image pinned by digest for amd64/arm64. This
recipe explicitly selects `--user-managed`: Solr 10 otherwise defaults to
SolrCloud. No ZooKeeper cluster, sample core, collection or documents are created.

## Setup and project integration

Set `SOLR_ADMIN_PASSWORD` to a random 64-character lowercase hexadecimal secret.
Install `solr`, open `http://localhost:11145/` and log in as `ods`.
`SOLR_PORT` changes the host port. Native BasicAuth and authorization grant this
initial account administrator access; anonymous access is blocked.

Create a core with the native Solr CLI/API and a configset appropriate to the
project, then index documents through its authenticated update API. The first
installation deliberately starts with no core: fields, analyzers and update
policies are project-specific decisions. User-managed mode uses cores rather
than SolrCloud collections. Consult the version 10 control-script reference for
`solr create` and credential options.

Local clients use `http://localhost:11145/solr/CORE`; authorized project containers
on `ods-network` use `http://solr:8983/solr/CORE`. Create scoped native users/roles
for projects rather than sharing the administrator. No Portal project credentials,
schema, embeddings or model dependencies are provisioned automatically.

## Security and persistence

`solr-data` persists `/var/solr`, including indexes, logs and native `security.json`.
Initial credentials use Solr's salted double-SHA256 format. Existing security
policy is preserved on restart. A mismatched configured administrator password
fails startup rather than silently resetting an existing deployment. Rotate via
Solr's native security API and update ODS configuration to match before restart.

Do not clear the volume to resolve an authentication problem. Back up indexes
with native backup tooling or stop the service before a consistent volume copy;
retain security configuration and application-specific configsets too.

Native UID8983 startup is retained with a read-only root, writable data volume
and bounded temporary filesystem. Heap is 1 GB within a 2 GB container limit,
with two CPUs. Larger indexes require explicit resource sizing. Linux Docker
Engine and Windows/macOS Docker Desktop Linux containers are supported packaging
targets; no GPU/model selection is required. Only HTTP loopback is published;
external deployment needs an explicit TLS/access configuration.

Authenticated Docker health checks query Solr system information. An anonymous
dashboard HTTP check is intentionally not used. This verifies server readiness,
not index correctness, query relevance or every native feature.

Recipe/schema/staging checks are not runtime verification. Image build, login,
core creation, indexing/query, credential rotation and restore/platform checks
remain pending. No containers/models were started during implementation.
