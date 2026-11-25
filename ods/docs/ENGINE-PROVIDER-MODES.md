# Engine Provider Modes

ODS has one default install path and several engine provider shapes.
This document names the supported contract before adding more provider-specific
installer behavior. It is a maintainer contract, not a new user-facing switch in
this change.

## Decision

ODS's default install remains the managed local stack. Provider modes
are supported alternatives that must prove the same product capabilities before
an install reports ready.

| Mode | Ownership | Intended use |
|------|-----------|--------------|
| `local` | ODS manages llama-server and its model files | Default local install path |
| `cloud` | ODS manages LiteLLM routing to remote APIs | CPU-only or remote-model installs |
| `hybrid` | ODS manages local-first plus cloud fallback routing | Local default with explicit fallback |

In `local` mode the model server runs in one of three places: ODS's
`llama-server` container (the default), an ODS-managed `llama-server` outside
the stack (the Windows Portal runs `llama-server.exe` on Windows while the
stack runs in WSL), or an OpenAI-compatible server that the owner runs and ODS
does not manage (Ollama, LM Studio, Lemonade Server or another). The env shape
must make that ownership explicit.

## Required Env Shape

Provider mode code uses these names:

| Variable | Meaning |
|----------|---------|
| `ODS_MODE=local` | Local inference, whichever of the three places runs the model |
| `LLM_BACKEND=llama-server` or `external` | Dashboard/API code uses llama-server's `/v1` API, or the external route |
| `NATIVE_LLM_BASE_URL` | Host-side origin of an ODS-managed llama-server outside the stack (no path) |
| `NATIVE_LLM_CONTAINER_BASE_URL` | The same server as containers reach it |
| `ODS_HOST_LLM_TRANSPORT` | `direct` or `model-router`: how the host agent reaches and verifies that server |
| `LLAMA_SERVER_API_KEY` | Bearer key of that server; LiteLLM, model-router and the host agent send it |
| `EXTERNAL_LLM_URL` | Host-side base URL of an owner-run server |
| `EXTERNAL_LLM_CONTAINER_URL` | The same server as containers reach it |
| `EXTERNAL_LLM_PROVIDER` | `ollama`, `lmstudio` or `openai-compatible` |
| `EXTERNAL_LLM_MODEL` | Exact model id the external server exposes |

The Lemonade-era names (`LEMONADE_*`, `ODS_MODE=lemonade`,
`LLM_BACKEND=lemonade`) are retired. Installers migrate them, and code reads
them only for one release; see
[AMD GPUs now run on llama.cpp](MIGRATION-LEMONADE-TO-LLAMACPP.md).

## Provider Capability Contract

A provider mode is ready only when every capability selected by the install
profile is reachable through the configured provider:

| Capability | Minimum proof |
|------------|---------------|
| Liveness | Health endpoint answers and reports an accepted version |
| Chat | A real chat completion succeeds through the same route apps use |
| Embeddings | An embedding request succeeds when RAG embeddings are enabled |
| STT | An audio transcription request succeeds when voice input is enabled |
| TTS | A speech generation request succeeds when voice output is enabled |
| Rerank | A rerank request succeeds when reranking is enabled |
| Stats | Dashboard can read throughput or returns an explicit unsupported state |

Unsupported selected capabilities must fail the install or readiness gate unless
the user explicitly chose a profile where that capability is optional. A skipped
selected capability is not a green install.

## Adapter Boundary

ODS should not spread provider-specific HTTP details across installer
phases, dashboard routers, compose templates, and probes. Each provider needs a
small adapter boundary that owns:

- URL normalization for host and container clients;
- API key/bearer header behavior;
- health, version, model list, and loaded-model detection;
- chat, embeddings, STT, TTS, rerank, and stats probes;
- error classification and recovery hints.

For ODS-managed llama-server runtimes, the host agent's runtime endpoint is
that boundary: one proof (`/health`, `/v1/models`, `/props` and a completion)
and one telemetry read (`/metrics`) serve the container, macOS, native Windows
and WSL-bridge runtimes.

## Security Contract

Provider modes must fail closed on network exposure:

- ODS must not bind an unauthenticated local engine to the LAN by
  default.
- If an engine is reachable outside loopback or a host-only bridge, bearer auth
  must be configured or the user must pass an explicit unsafe override.
- All ODS-owned clients must pass the configured provider key before key
  enforcement is enabled by default.
- Readiness must distinguish "provider unreachable", "auth rejected", "model
  missing", and "capability unsupported".

## Compose Contract

Provider modes may replace managed services, but only through the compose
resolver. A provider mode must define which services are:

- owned by ODS;
- replaced by the provider;
- optional and absent;
- still enabled through user extensions.

After extension changes, dashboard actions, `ods restart`, and lifecycle
commands, the resolver must regenerate the same provider-aware compose surface.
Provider mode support is not complete if a later restart revives services the
mode intentionally replaced.

## Compatibility Policy

ODS should install or recommend a known-good provider version, but it
must also handle an already-installed provider conservatively:

- accept the known-good pin and documented version floor;
- accept newer versions only after provider contract probes pass;
- reject older versions with a targeted recovery message;
- keep a CI/mock contract for API shapes and a fleet contract for real hardware.

An external provider can change its model ids, health payloads, packaging and
auth semantics upstream. Those are provider-contract changes, not ad hoc
installer quirks.

## Non-Goals

- This contract does not make an external provider the default install path.
- This contract does not remove the existing local, cloud, or hybrid modes.
- This contract does not require ODS to own every provider process.
- This contract does not bless a hidden fork or product variant outside main.

## Validation Expectations

Provider-mode PRs should add validation at the layer they touch:

- docs-only contract changes: markdown link checks;
- adapter changes: mocked provider API unit tests;
- installer changes: platform contract tests and dry runs;
- compose changes: resolver tests for restart/cache regeneration;
- dashboard changes: readiness/features/status tests for default and provider
  modes;
- release candidates: distro lab plus real-hardware fleet validation.
