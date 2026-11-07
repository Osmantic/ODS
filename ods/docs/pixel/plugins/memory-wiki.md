# Review: memory-wiki (Knowledge wiki)

Reviewed for ODS's Portal plugin catalog on 2026-10-26. This review covers the
plugin exactly as it ships inside OpenClaw 2026.6.33. That is the package Pixel
installs: `openclawPackage` in `vendor/pixel/RELEASE-MANIFEST.json`, SHA-256
`96880d5a9fe3ef6b3d2ec0ac016e48f99fff7b1af4e22b7c814db6357c163952`.

memory-wiki is a core OpenClaw plugin, so ODS adds no bytes for it. The
plugin's code, including the shared chunks it imports, comes from that pinned
package. If Pixel moves to another OpenClaw, `tests/test_pixel_plugins.py`
fails until this review is repeated for the new version.

## What was read

The compiled code that runs, read as text from the pinned package:

| File | SHA-256 |
|---|---|
| `dist/extensions/memory-wiki/index.js` (entry, tools, gateway methods) | `676a10132a7e25f8b8407f07f08e4106094c25f557ecd52e65836bc7acb3be27` |
| `dist/cli-DwNmF7SN.js` (the wiki engine: vault, compile, search, apply, Obsidian, CLI) | `2ece5f0c272fe5a19dae3418fff398da8028716686c64266537c4d2f48c8fb3f` |
| `dist/config-CUjuK5-Y.js` (configuration schema and defaults) | `c746402e58e314d6f9af086baba2be67c186fb6d4b17d023f3b20724f6be5a7c` |

The review checked every place this code:

- registers anything with OpenClaw;
- starts a process;
- reaches the network;
- reads or writes a file path.

It also checked what each configuration switch turns off. It was not a
line-by-line review of the whole engine.

## What the plugin registers

- **Five agent tools:** `wiki_status`, `wiki_search`, `wiki_get`, `wiki_apply`,
  `wiki_lint`. They run in Portal's gateway, on the host.
- **Nineteen gateway methods** (`wiki.*`):
  - Each needs an operator scope (`operator.read` or `operator.write`; ingest has
    its own local-file scope), so only a holder of the gateway token can call them.
  - Portal's sandboxed agent holds no such token.
- **A memory prompt supplement:**
  - With the compiled digest off, it is fixed text that tells Portal how to use
    the wiki tools. It appears only while those tools are allowed and never
    changes between turns.
  - A memory corpus supplement lets `memory_search` include wiki pages, searched
    locally.
- **The CLI** `openclaw wiki …`, for operators.
- **Companion skills:** these stay hidden, because Portal runs with `skills: []`.

## Findings

- **Network:**
  - The plugin's code makes no network calls.
  - URL ingest is a configuration switch that this version does not implement:
    ingest reads only a local file, and only through an operator gateway method
    or the CLI.
- **Processes:**
  - The only process it starts is the `obsidian` binary from `PATH`, with
    arguments and no shell, and only from operator gateway methods or the CLI.
  - ODS hosts do not install it.
  - The `wiki_status` tool only checks whether the binary exists.
- **Files:**
  - The tools read and write only inside the vault. Mutations write `syntheses/`
    pages or an existing page, through OpenClaw's guarded file API, which refuses
    symbolic links.
  - Page listing skips a page that is a link, but it follows a linked folder, and
    the compiled digests are read without a link check.
  - So the vault must never sit inside Portal's workspace. A sandboxed Portal can
    create links there, and the host-side plugin would follow them out of the
    sandbox.
- **Prompt:** with `context.includeCompiledDigestPrompt` off, no page content
  enters Portal's prompt by itself.

## The configuration ODS locks

```json
{
  "vaultMode": "isolated",
  "vault": {"path": "~/.openclaw/wiki/portal", "renderMode": "obsidian"},
  "obsidian": {"enabled": false, "useOfficialCli": false, "openAfterWrites": false},
  "bridge": {"enabled": false},
  "unsafeLocal": {"allowPrivateMemoryCoreAccess": false, "paths": []},
  "ingest": {"autoCompile": true, "maxConcurrentJobs": 1, "allowUrlIngest": false},
  "search": {"backend": "local", "corpus": "wiki"},
  "context": {"includeCompiledDigestPrompt": false},
  "render": {"preserveHumanBlocks": true, "createBacklinks": true, "createDashboards": true}
}
```

- **The vault:** `~/.openclaw/wiki/portal` sits beside Portal's workspace
  (`~/.openclaw/workspace-pixel`), which is the only host folder the sandbox
  mounts.
- **Isolated mode:** nothing is imported from memory or from local paths.
- **Obsidian:** Obsidian-friendly Markdown, with the Obsidian app integration off.

## Residual risk, accepted

- **Wider searches:** a tool call may ask `wiki_search` or `wiki_get` for the
  shared memory corpus. That reaches only Portal's own memory, with OpenClaw's
  session-visibility checks, which `memory_search` already gives Portal.
- **Full Access:** Portal runs as the owner and can reach the vault and every
  other owner file anyway. The connected-apps Full Access warning covers this.

## Before release

Fleet check on Linux and WSL:

- turn the plugin on;
- ask Portal to file and find a fact;
- confirm the pages appear under `~/.openclaw/wiki/portal`;
- turn the plugin off;
- confirm Portal no longer lists the `wiki_*` tools and the pages remain.
