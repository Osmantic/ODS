# Pixel source provenance in ODS

The source under `vendor/pixel/` was exported from the private
`Osmantic/Pixel` repository at commit
`b33730436baf5d98bf58f7d57c090318fe19f433`, then adapted for ODS's
public ODS-only license, bundled-source installation, documentation, and
22-tool integration. The export deliberately omits the private Git history,
the private repository's `.github` workflows, the historical `LIVE-AUDIT*`
documents, and `DREAM-FORGE-SOURCE-AUDIT.md`. It is not a live Git submodule.

The visible source is duplicated into `vendor/pixel.bundle` solely so existing
Pixel installation code can use exact-commit Git verification without network
or private credentials. The bundle contains one new synthetic root commit with
public Osmantic release identity and no ancestors. Its commit is
`308ceeee9a3c53466aff3905ef7fc0e0ad6163b0`, and its SHA-256 is
`101281a9972a7b6fb65622b2cbafe3907adadb5b4ed9bed7dbe7b693d2e62ff3`.
Run `python3 scripts/verify-pixel-bundle.py` to check the bundle against the
visible source, tracked executable modes, and those pins. The `pixel` launcher
and the install/bootstrap scripts must retain executable Git modes.

The repository owner authorized this source publication and the ODS-only
Pixel grant. Third-party packages are not included in the Git bundle and
retain their own notices and licenses.

The ODS lint maintenance adaptation retains import effects and exported names,
renames only unread local bindings without dropping their right-hand sides,
expands semicolon statements with AST-equivalence verification, removes one
identical shadowed test helper, and supplies two missing Python imports. The
bundle was regenerated twice from tracked source bytes and executable modes;
both artifacts matched. The public synthetic author, message, and original
synthetic timestamp are retained; no upstream/private history is included.

The ODS gateway-extension capacity adaptation raises the configure and renderer
per-extension limit from 24 to 32 tools, retaining name, digest, path and duplicate
validation. Native boundary tests cover 25 and 32 accepted tools and 33 rejected;
the ODS installer additionally configures the pinned bundle using its actual
generated extension tool list. The bundle retains its public synthetic identity
and timestamp, contains one root commit, and two regenerations were byte-identical.

## Pending upstream change: Anthropic work-provider model

Pixel's Anthropic work-provider lane is pinned to `claude-sonnet-4-5-20250929`.
Anthropic retires that snapshot no sooner than 2026-09-29. The profile's
`modelSelection` is `fixed`, and an owner-private policy may only repeat
`defaultModel`, so ODS cannot override it without regenerating this bundle.
The lane is off unless an owner-private policy enables it with the owner's own
Anthropic key; ODS never does. Portal chat uses LiteLLM's `ods/current` route.
The upstream Pixel fix is to change the ID to `claude-sonnet-4-6` in:

- `deploy/work-provider/profiles/anthropic.json` (`defaultModel`)
- `deploy/work-provider/neutral-corpus.mjs` (`ANTHROPIC_MODEL`)
- `deploy/work-provider/provider-smoke-core.mjs` (the `anthropic` smoke model)
- `tests/provider-ingress-smoke.test.mjs`, `tests/work-provider-adversarial.test.mjs`
  and `tests/work-provider-qualification-remote-lanes.test.mjs`
- `deploy/work-provider/README.md` and `CHANGELOG.md`

Owners who enabled the lane must update any `model` in their policy and requalify
the lane; the router rejects a qualification for another model. Pixel's OpenRouter
profile has a placeholder `anthropic/claude-sonnet-4-5` default, which is never
sent because that lane is owner-pinned. `tests/test-cloud-model-ids.py` tracks
both IDs and fails once a re-vendor drops them.

## Retired workspace guidance

Through 4.3.28, `workspace-template/AGENTS.md` carried one retired section, and
`MEMORY.md` one matching entry, describing a maintainer's private multi-machine
workflow. They arrived with ODS #6156 and applied to no user installation.
Pixel 4.3.29 removes them. The template carries neutral model-routing guidance
in their place and is byte-identical to what the installer migration below
produces from the 4.3.28 files.

Workspaces created from older releases keep their files, because Pixel copies
template files only where a workspace lacks them. For those workspaces:

- `installers/lib/pixel-workspace-guidance.py` replaces the exact retired bytes
  with the same guidance: in the generated workspace at configure time, and in
  an existing owner workspace before the gateway restarts. Edited copies stay in
  place and are reported for review. The original files of an owner workspace
  are kept in a private `.ods-workspace-guidance-backups/` directory beside the
  workspace, outside what the agent reads.
- The `pixel-ods` plugin's bootstrap hook presents a workspace that still holds
  the exact 4.3.28 `AGENTS.md` as the 4.3.29 default, in memory only, before it
  scopes Calendar and Frontier guidance.
- `tests/test-seeded-content-privacy.py` fails CI if owner-private or
  fleet-specific text reaches the template by any route, or if the shipped
  template would need the migration again.

## ODS access-release coordination adaptation

This change adds the public installer-only access proof and release-transaction
helpers and forwards the exact held transaction through apply/verify. It does not
change the original upstream export identity described above.

The replacement bundle was generated locally from the visible public ODS vendor
source and its tracked executable modes, using the same synthetic single-root
packaging procedure and metadata required by `scripts/verify-pixel-bundle.py`.
The retained Osmantic packaging author/message and synthetic timestamp are
reproducibility metadata, not an upstream signature or approval of these edits.
No private repository, private history or signing credential was used. Two
independent regenerations produced identical bytes; every source blob and mode
was verified. Existing source-digest and installer custody checks remain active.

## ODS-maintained 4.3.28 source-upgrade candidate

The 4.3.28 bundle advanced the public ODS-maintained source to 4.3.28 so the
existing increasing-version checks can reconcile the access/release helpers
without changing an installed same-version release in place. It does not claim
an upstream/private Pixel release. The functional source checkpoint is
`62ac4f546d356c3897c2587381e2686a1393f6e9`;
`pixel/ODS-QUALIFICATION-4.3.28.md` records the tests and remaining physical
qualification limits. The previous compatibility records remain historical.

Two independent builds produced byte-identical bundles and verified every blob
and Git executable mode against the public source. The source contains 1,307
files, including 96 executable entrypoints; only the single synthetic root
commit is advertised. Runtime versions, dependency versions, image digests and
trust anchors are unchanged. No release signature was generated and no private
repository was accessed. Existing clients prepared with the previous public
4.3.27 bundle remain readable through their exact retained receipt identity.

The 4.3.28 candidate was rebuilt before publication to preserve the Portal QA
waiting-plan expiry correction. Both paused and approval-pending expired plans
settle without execution; all 55 broker tests passed. Two independent builds
verified identical bundle bytes and every source blob/mode. This replaces an
unpublished candidate and does not claim a protected live broker upgrade.

The draft PR's 4.3.28 candidate was rebuilt to correct public compatibility
documentation. Absent historical audits are explicitly marked unavailable;
existing evidence links and historical JSON metadata remain intact. The two
targeted generation tests and documentation link check passed. Two independent
builds verified identical bundle bytes and every source blob/mode. This replaces
the earlier draft candidate; it does not claim a protected live broker upgrade.

## ODS-maintained 4.3.29 source release

This release removes the retired workspace guidance described above. It changes
only workspace template text and version metadata; runtime code, OpenClaw,
plugin, Node and dependency versions, image digests and trust anchors are
unchanged. The version moves to 4.3.29 because an existing installation refuses
different source at the version it already runs, and the held source upgrade
requires a strictly newer version for a changed source. The functional source
checkpoint is `50762d145adf245b4c32bdf0b40f5eee8359a510`;
`pixel/ODS-QUALIFICATION-4.3.29.md` records the automated evidence and the fleet
qualification that is still pending. The release stays a `candidate` until the
held source upgrade is qualified on real Linux/WSL installations, in Sandbox
mode and with Full Access, and through a macOS native update.

The bundle was built from the committed `vendor/pixel` tree
`4dbf58eccdd25806e9d955a47c3109561eda14c5`. An empty bare repository whose
`objects/info/alternates` names this repository's object store received
`git commit-tree` of that tree with the retained synthetic author, committer,
timestamp and message; `HEAD` was pointed at that commit, and
`git -c pack.threads=1 bundle create <file> HEAD` wrote the bundle. Two builds
were byte-identical, and builds with the default and four pack threads matched
them (Git 2.53 on Windows). The bytes depend on the packs in the object store
that the alternates name; the commit identity depends only on the tree. The
source contains 1,308 files, including 96 executable entrypoints, and only the
single synthetic root commit is advertised. `scripts/verify-pixel-bundle.py`
verified every blob and Git executable mode against the public source. No
release signature was generated and no private repository was accessed. Clients
prepared with the 4.3.28 bundle remain readable through their exact retained
receipt identity.

## ODS-maintained 4.3.30 Operations startup candidate

This candidate waits for the Operations Broker inventory before applying
the existing reader ACL checks. A real WSL clean install exposed the
missing projection; a controlled delayed-file test reproduces the old
failure and accepts the new wait. `pixel/ODS-QUALIFICATION-4.3.30.md`
records the evidence and remaining live qualification. The version
advance preserves the existing strictly increasing source-upgrade gate.

The public vendor tree is `4199870a91472cdba7c023fd36aba6a4bac05688`. Two independent empty bare
repositories used the same alternates-based procedure documented above
with the retained synthetic identity, timestamp and root message. The
two bundles were byte-identical. Only the single public root is advertised;
no private history or signing credentials were accessed. Runtime and
dependency versions, image digests, policies and trust anchors are unchanged.
Current source/bundle pins above identify this candidate; prior release
sections remain historical evidence.
