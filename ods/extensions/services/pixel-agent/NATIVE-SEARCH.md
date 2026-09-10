# Native search installation

Fresh Pixel installations use OpenClaw's `parallel-free` provider. Basic search
does not require Perplexica, a SearXNG application, or a search API key. It does
require Internet access to the external provider; this is not offline search.
Existing onboarding retains its provider, including legacy SearXNG installations.
An owner can explicitly select `PIXEL_WEB_SEARCH_PROVIDER=searxng` or
`PIXEL_WEB_SEARCH_PROVIDER=parallel-free` when installing/reconfiguring Pixel.

The installer provisions the official `@openclaw/parallel-plugin` version
`2026.6.33` from its fixed npm release URL, verifies its pinned SHA-512 archive,
and publishes a complete immutable directory. Pixel computes and verifies the
extension's canonical SHA-256 tree digest. Reuse checks all files and directories;
unexpected existing content causes an actionable failure and is retained for
inspection. No provider implementation is copied into ODS.

The search provider is independent of the model gateway and the browser. Selecting
it does not grant host execution, change access mode, or enable browser navigation.
Perplexica remains available as an optional research application. The paid
OpenClaw provider ID `parallel` is distinct from `parallel-free`.

Reconfiguring the same model route and context preserves the owner's output-token
budget, including when the gateway credential rotates. Explicit model-setting
changes still take effect. Model reconciliation and its rollback snapshot retain
additional extensions with unique IDs and bound paths/digests, so enabling native
search does not prevent subsequent model changes.

Public source downloads are available during ordinary research and development
without classifying the owner's wording as an Operations task. The broker still
enforces its public-network, redirect, size, and quarantine policy. Status,
events, and cancellation apply only to downloads actually submitted by the run;
the existing promoter independently verifies the broker receipt and rehashes a
create-only workspace copy. Download completion does not end the research task
or prevent subsequent sandbox work, and it grants no host-command or remote
transfer authority. Explicit byte-for-byte requests retain their source and
digest binding.

The paired Pixel source candidate is tracked in
[Pixel PR #240](https://github.com/Osmantic/Pixel/pull/240). Source and disposable
runtime tests do not establish installed chat or fresh-install acceptance. Keep
[ODS PR #3385](https://github.com/Osmantic/ODS/pull/3385) open until user acceptance.

## Attribution

The provisioned Parallel plugin is part of the official
[OpenClaw 2026.6.33 release](https://github.com/openclaw/openclaw/tree/v2026.6.33/extensions/parallel).
Its upstream implementation and dependencies remain governed by their original
terms. OpenClaw is copyright (c) 2026 OpenClaw Foundation and is MIT licensed.
The full upstream MIT notice is retained in ODS's distributed
[third-party notices](../../../docs/pixel/upstream/THIRD_PARTY_NOTICES.md).
See the [upstream license](https://github.com/openclaw/openclaw/blob/v2026.6.33/LICENSE)
and [provider documentation](https://github.com/openclaw/openclaw/blob/v2026.6.33/docs/tools/parallel-search.md).
