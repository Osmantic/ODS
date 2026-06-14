# Engine Provider Modes

Dream Server has one default install path and several engine provider shapes.
This document names the supported contract before adding more provider-specific
installer behavior. It is a maintainer contract, not a new user-facing switch in
this change.

## Decision

Dream Server's default install remains the managed local stack. Provider modes
are supported alternatives that must prove the same product capabilities before
an install reports ready.

| Mode | Ownership | Intended use |
|------|-----------|--------------|
| `local` | Dream Server manages llama-server and its model files | Default local install path |
| `cloud` | Dream Server manages LiteLLM routing to remote APIs | CPU-only or remote-model installs |
| `hybrid` | Dream Server manages local-first plus cloud fallback routing | Local default with explicit fallback |
| `lemonade` | A Lemonade engine provides OpenAI-compatible local APIs | Supported local engine mode, not the default |

The Lemonade provider may be either Dream-managed on platforms where that is
already supported, or external/unmanaged when Dream Server adopts an existing
host-native Lemonade service. The env shape must make that ownership explicit.

## Required Env Shape

Provider mode code should converge on these names:

| Variable | Meaning |
|----------|---------|
| `DREAM_MODE=lemonade` | Dream Server is running with the Lemonade provider |
| `LLM_BACKEND=lemonade` | Dashboard/API code should use Lemonade-compatible API paths |
| `LEMONADE_EXTERNAL=true|false` | Whether Dream Server owns the Lemonade process lifecycle |
| `LEMONADE_BASE_URL` | Host-side Lemonade base URL used by installers and host tools |
| `LEMONADE_CONTAINER_BASE_URL` | Container-side Lemonade base URL used by compose services |
| `LEMONADE_API_BASE_PATH=/api/v1` | Lemonade OpenAI-compatible API path |
| `LEMONADE_MODEL` | Chat model id routed through LiteLLM |
| `LEMONADE_API_KEY` | Optional bearer key for engine calls |

Installers may retain legacy variables during migration, but new behavior should
read and write the canonical names above.

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

Normal dashboard polling must remain passive: it may read health, model catalog,
loaded-model state, and stats, but it must not trigger inference or model
downloads. A passive check may mark a route ready only when the provider reports
the configured model as loaded with the expected capability type. Real chat,
embedding, rerank, STT, and TTS proofs run only through an explicit authenticated
active-probe action because those requests may load or switch provider models.

## Adapter Boundary

Dream Server should not spread provider-specific HTTP details across installer
phases, dashboard routers, compose templates, and probes. Each provider needs a
small adapter boundary that owns:

- URL normalization for host and container clients;
- API key/bearer header behavior;
- health, version, model list, and loaded-model detection;
- chat, embeddings, STT, TTS, rerank, and stats probes;
- error classification and recovery hints.

For Lemonade, this means calls to `/api/v1/health`, `/api/v1/models`,
`/api/v1/chat/completions`, `/api/v1/embeddings`, `/api/v1/audio/*`,
`/api/v1/reranking`, and `/api/v1/stats` should be centralized before more
platform-specific behavior is added.

## Security Contract

Provider modes must fail closed on network exposure:

- Dream Server must not bind an unauthenticated local engine to the LAN by
  default.
- If an engine is reachable outside loopback or a host-only bridge, bearer auth
  must be configured or the user must pass an explicit unsafe override.
- All Dream-owned clients must pass the configured provider key before key
  enforcement is enabled by default.
- Readiness must distinguish "provider unreachable", "auth rejected", "model
  missing", and "capability unsupported".

## Compose Contract

Provider modes may replace managed services, but only through the compose
resolver. A provider mode must define which services are:

- owned by Dream Server;
- replaced by the provider;
- optional and absent;
- still enabled through user extensions.

After extension changes, dashboard actions, `dream restart`, and lifecycle
commands, the resolver must regenerate the same provider-aware compose surface.
Provider mode support is not complete if a later restart revives services the
mode intentionally replaced.

## Compatibility Policy

Dream Server should install or recommend a known-good provider version, but it
must also handle an already-installed provider conservatively:

- accept the known-good pin and documented version floor;
- accept newer versions only after provider contract probes pass;
- reject older versions with a targeted recovery message;
- keep a CI/mock contract for API shapes and a fleet contract for real hardware.

The Lemonade provider is especially sensitive to upstream changes in model ids,
backend recipe names, health/stat payloads, installer packaging, and auth
semantics. Those are provider-contract changes, not ad hoc installer quirks.

## Non-Goals

- This contract does not make Lemonade the default install path.
- This contract does not remove the existing local, cloud, or hybrid modes.
- This contract does not require Dream Server to own every provider process.
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
