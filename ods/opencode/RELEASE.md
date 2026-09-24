# OpenCode release and research support

ODS installs the reviewed stable OpenCode **1.18.32** release on Linux, macOS,
and Windows. `installers/lib/opencode-release.tsv` is the shared asset/SHA256
manifest. Version constants used by installer messages must match it.

Source: [upstream release](https://github.com/anomalyco/opencode/releases/tag/v1.18.32),
published 2026-09-21. The release tag resolves to commit
`545f51d26cc39a907d2867492d498d9607ea5fa4`. The release API's
`target_commitish` is different; use the tag commit for source inspection.
The archive digests come from the upstream GitHub release assets. x64 uses the
baseline build so hosts without AVX2 remain supported; ARM uses native builds.

Reinstallation upgrades older binaries. A matching existing version is reused;
otherwise ODS downloads and verifies the archive, extracts into an owned staging
directory, and executes `--version` before replacing `~/.opencode/bin/opencode`
(or `opencode.exe`). Other package-manager binaries are not overwritten.
Download, checksum, extraction, or version failures preserve the previous binary.
A locked Windows binary also fails without terminating its process: finish active
work and rerun the installer. Configuration/session directories are not removed.
The existing ODS model-route configuration migration still runs after success.

ODS launchers enable the upstream `websearch` tool for local model providers via
`OPENCODE_ENABLE_EXA=1` and `OPENCODE_WEBSEARCH_PROVIDER=exa`. This searches the
public web through `https://mcp.exa.ai/mcp`, which upstream supports without an API
key. Existing OpenCode permissions still apply. These environment settings belong
to the ODS service/launcher, not the user's global shell. Upstream 1.18.32 does not
offer a native SearxNG provider setting; enabling the tool does not establish that
the search endpoint is reachable or that a research answer is correct.

Qualification must record the actual version, executable hash, model/provider,
and the **selected provider's** tool registry. Verify a real public search and its
sources independently; the global tool-ID list alone is not evidence of search
availability. Older-version comparison receipts remain historical results.

To advance the standard version, review the canonical stable release and tag,
update the shared asset digests and message constants, run the runtime upgrade
tests on Unix and Windows, then fresh-install/qualify the fleet. Do not update a
binary while a qualification or comparison session is active.
