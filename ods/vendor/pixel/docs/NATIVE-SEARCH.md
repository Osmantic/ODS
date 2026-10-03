---
title: Native search providers
doc_type: reference
audience: [operator, contributor, maintainer]
feature_status: candidate
owners: [runtime]
sources_of_truth: [scripts/configure.mjs, scripts/render-config.mjs, scripts/preflight.sh, tests/test_native_search.py]
last_verified_at: 2026-09-09
---

# Native search providers

Onboarding accepts `webSearchProvider` with `searxng` (the existing default) or
`parallel-free`. Selecting `parallel-free` uses OpenClaw's official Parallel
search provider without an account or API key. It does not require the SearXNG
application, Perplexica, or another application to perform research.

The installer must first provision a compatible, reviewed
`@openclaw/parallel-plugin` package. Include its `parallel` plugin ID, absolute
directory, and approved SHA-256 tree digest in `gatewayExtensions`. The directory
must satisfy the existing extension rules: outside gateway-writable state,
workspace and cache paths, with no symbolic links and matching plugin identity
and content digest. An ID-only extension is insufficient. This feature does not
download or approve an arbitrary package during configuration.

The selected provider is recorded in deployment metadata and generated settings,
then used during rendering and preflight. Reusing the onboarding answers preserves
the selection. Existing onboarding files retain SearXNG behavior. The paid
provider ID `parallel` is distinct from `parallel-free` and is not accepted by
this onboarding option.

For `parallel-free`, the renderer omits SearXNG registration and preflight does
not require its URL or contact its endpoint. Existing pinned package-integrity,
model endpoint, service, release and sandbox checks remain. The unused pinned
SearXNG plugin package is still part of Pixel's bootstrap release; it does not
require a running SearXNG application.

This option selects a provider; it does not enable a disabled Web limb, provision
a browser, or establish search quality. Installation qualification must cover the
actual search tool in chat, source inspection, errors and retry behavior, and
reconfiguration with the other applications absent. Provider service availability
still affects live search.

The implementation uses OpenClaw's documented extension interface. No provider
implementation is copied into Pixel. See the upstream
[Parallel search documentation](https://github.com/openclaw/openclaw/blob/main/docs/tools/parallel-search.md)
and the pinned [OpenClaw license and notices](../THIRD_PARTY_NOTICES.md).
Distributors must retain the notices required by the packages they provision.
