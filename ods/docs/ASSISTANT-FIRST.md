# Assistant First

Assistant First is an opt-in, Linux-first public-beta installation profile. It
is intended for a fresh ODS installation whose first job is a generic,
user-named assistant rather than a preinstalled collection of applications.

Run it from a reviewed checkout:

```bash
./install.sh --assistant-first
```

The profile currently requires a host qualified for the assistant runtime and
its separately accepted license. It does not change the default installer
choice. It also refuses to convert an existing Full, Core, or Custom
installation because silently removing optional applications from an active
Compose graph could stop containers the owner still uses.

## Candidate minimum graph

For managed local inference the resolver selects:

- `docker-compose.base.yml`;
- the detected CPU, NVIDIA, AMD, or Intel inference overlay; and
- `extensions/services/pixel-edge/compose.assistant-first.yaml`.

The resulting container graph contains `dashboard`, `dashboard-api`,
`pixel-edge`, `llama-server`, and provisionally `model-router`.
`pixel-edge` is an internal compatibility identifier, not the assistant's
public name. Cloud and external-provider modes replace the managed inference
services with their single selected route.

`model-router` remains provisional until a real installed journey proves the
assistant, model switching, and recovery paths can operate without it.

## What is absent

Open WebUI, SearXNG, Perplexica, remote-provider transport, voice, RAG,
workflows, image generation, observability, privacy tools, and other optional
applications are structurally absent from the resolved first-boot graph. Image
discovery reads that exact graph, so an unselected application's image is not
pre-pulled.

Full, Core, and Custom continue to use the legacy resolver behavior. The
services extracted from `docker-compose.base.yml` remain enabled there through
their manifest-owned Compose fragments.

## Update recovery state

Normal `ods update` image refreshes and source-checkout updates use the same
owner-private, schema-versioned rollback snapshot. Creation fails closed before
environment rewrites, image pulls, or checkout mutation. The snapshot records
exact presence, content hashes, and restore custody for environment and Compose
selection, generic `config/`, the extension desired-state lockfile, transaction
journals and finalization receipts, and `data/user-extensions` definitions and
receipts. Snapshot validation, path allowlisting, symlink rejection, and a
staged payload copy all complete before a manual rollback stops services or
changes files.

Committed definition rollback points under `data/user-extensions/.backups`
are durable state and are included. In-flight `data/user-extensions/.tmp`
staging is explicitly excluded and removed by exact rollback. Ordinary user
data backups also preserve nonsecret remote-provider routing and
pixel-inference owner state while excluding remote-provider secrets, generated
dashboard credentials, and secret-bearing environment backup history.

`data/assistant-first/secrets` is deliberately not copied. It remains in place
under host custody while lockfiles and transaction records carry references
only. Older pre-v2 rollback snapshots remain readable with an explicit warning,
but they cannot provide the v2 checksum guarantee.

The first pre-update gate is also a pure, hash-bound compatibility assessment.
It binds the exact current lockfile, candidate ODS version, and verified
candidate catalog revision. Every enabled locked extension must remain present
in the candidate catalog. Immutable same-version definition drift and catalog
version regressions fail closed. When the locked compatibility range excludes
the candidate core, only a newer candidate definition whose range includes that
core may become a required extension upgrade. Disabled incompatible entries are
reported without silently enabling or upgrading them.

This assessment performs no mutation and does not itself authorize an extension
or core update. Candidate checkout resolution, exact composite upgrade planning,
mutation quiescence, combined core/desired-state commit, health qualification,
and coordinated rollback remain later Phase 5 gates.

## Evidence boundary

The source contract checks resolver ordering, the exact candidate service set,
capability declarations, Compose rendering, and legacy graph equivalence.
These checks do not claim that an installed assistant journey has passed.
Installed download, readiness, idle-resource, chat, and lifecycle evidence is
required before Assistant First can become the recommended default.
