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
`5154ac8e97a38cf77f2768c9f6021bd02d9ab574`, and its SHA-256 is
`7403132329bef463cc864855136e99223d0ff9c52279329c7ab710f9837614c5`.
Run `python3 scripts/verify-pixel-bundle.py` to check the bundle against the
visible source, tracked executable modes, and those pins. The `pixel` launcher
and the install/bootstrap scripts must retain executable Git modes.

The repository owner authorized this source publication and the ODS-only
Pixel grant. Third-party packages are not included in the Git bundle and
retain their own notices and licenses.

The current Portal runtime candidate uses internal package version 4.3.40.
Its functional checkpoint is `ad8733c2ca757256c7b98ae6aec936a41d6aff74`;
the versioned source checkpoint is `2902c181e0974ecbdbdd7fb65cbdf24c3bd70b18`.
Email summaries exclude attachment parts and their subtrees while retaining
declared attachment metadata. Revision 3 invalidates older summary caches and
cursors, and every Gmail reader requires that exact projection revision before
exposing records. Controlled MIME/cache/reader tests and independent focused
review passed. The disclosure distinguishes summary content from metadata and
selected cloud-model sharing; Gmail can still transport inline bytes before
filtering, and old histories/backups are not rewritten.
Two independent public single-root builds match. Exact source/digest/Git-mode
verification and the exact-bundle plan/apply/verify/rollback fixture passed.
The ordinary installed upgrade from `947387494` completed with the original
read-only Google grant and 9B/64K model preserved, ready/verified runtime and
healthy core containers. Installed broker/reader bytes match revision 3. The
same natural count request then returned one Inbox summary and an exactly
covered seven-day Calendar read through native cron; its answer distinguished
indexed mail from unavailable mailbox totals. The scoped owner HTTP and service
checks do not certify browser/UI, real-attachment payloads, native macOS or
general model reliability. The preparation record is
`pixel/ODS-QUALIFICATION-4.3.40.md`.

## Prior Source-argument candidate 4.3.39

The preceding Portal runtime candidate uses internal package version 4.3.39.
Its functional checkpoint is `069b43b62b3fb589673c4d31f0b4dface6a514a4`;
the versioned source checkpoint is `326a16832cb4ac28507dfddb53a3a763ad283745`.
The Source handler validates its declared schema with the existing locked
TypeBox before reading projections or creating proposals. Unknown arguments
cannot become omitted parameters with a broader default. Five regressions fail
on the previous index and pass on this code; the actual pinned SDK preserves
the unknown argument until this refusal and accepts a corrected one-record call.
Two independent public single-root builds match. Exact source/digest/Git-mode
verification and the exact-bundle clean-room plan/apply/verify/rollback fixture
passed. The ordinary installed upgrade from `9d9658f57` subsequently completed
with the original read-only Google grant and model preserved, verified runtime
and healthy core containers. One actual model request recovered from an invalid
Gmail call through schema description and returned exactly one summary with a
covered seven-day Calendar read. This scoped Windows/WSL result does not certify
general model reliability, native macOS or full public release acceptance. The preparation record
is `pixel/ODS-QUALIFICATION-4.3.39.md`.

## Prior email-count candidate 4.3.38

The preceding functional checkpoint is `1e40a5add3d5725372ef27ec987bd8e650e9bd5f`.
Gmail page results now distinguish the local index, configured-query coverage
and unknown mailbox totals. Two independent single-root builds matched; exact
source/digest/executable-mode verification and the bundle-checkout plan/apply/
verify exercise passed. Dependency/image pins and trust anchors are unchanged.
The synthetic 9B model check still found an incorrect freshness statement;
correct tool metadata is not proof of final-answer consistency. The ordinary
installed upgrade through `2486daa6a` completed, with scoped real read evidence
recorded in the Portal integration status; native macOS/fleet acceptance remains
open. The reviewed comparison prompt's original spacing and unchanged
checksum are restored; the upstream release contract still requires historical
audit documents deliberately omitted from this public export. See
`pixel/ODS-QUALIFICATION-4.3.38.md` for the scoped evidence and limits.

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

The public vendor tree is `b4a2db3cfc6d3d6388c57f94dc6befaf2ed5a292`. Two independent empty bare
repositories used the same alternates-based procedure documented above
with the retained synthetic identity, timestamp and root message. The
two bundles were byte-identical. Only the single public root is advertised;
no private history or signing credentials were accessed. Runtime and
dependency versions, image digests, policies and trust anchors are unchanged.
Current source/bundle pins above identify this candidate; prior release
sections remain historical evidence.

## ODS-maintained 4.3.31 Operations executable-start candidate

This candidate separates systemd filesystem-isolation setup from Operations
inventory readiness. Type=exec and a bounded 180-second startup window preserve
every existing isolation setting. The existing inventory and private-state
checks remain required. See `pixel/ODS-QUALIFICATION-4.3.31.md` for measured
WSL behavior and the boundary between fixture and fleet acceptance.

The public vendor tree is `3c8abd7b0f95ffdb72d0f97685d89ef81b927eb1`. Two independent empty bare
repositories produced byte-identical bundles using the documented synthetic
identity, timestamp and message. Only one public root is advertised. Runtime
and dependency versions, image pins, policies and trust anchors are unchanged.
Current source/bundle pins above identify this candidate; prior sections remain
historical evidence. No private source history or signing credentials were used.

## ODS-maintained 4.3.32 sandbox Node.js candidate

This candidate installs the official Node.js 24.21.0 `node` binary in the agent
sandbox image, pinned per architecture by SHA-256 in `RELEASE-MANIFEST.json`
(`sandboxNodeRuntime`), without npm, npx or corepack. Bootstrap and native macOS
preparation prove the image's node offline before any live change. The pins
were read from the release's `SHASUMS256.txt`; its clearsigned
`SHASUMS256.txt.asc` verified with the Node.js release key
`5BE8A3F6C8A5C01D106C0AD820B1A390B168D356`, which the Node.js README lists.
See `pixel/ODS-QUALIFICATION-4.3.32.md` for the change, its evidence and the
fleet qualification still required.

The public vendor tree is `38f08da83aa844c1363ef2629fa31e250806f9b9`. Two
independent empty bare repositories produced byte-identical bundles with the
alternates-based procedure, synthetic identity, timestamp and message
documented above (Git 2.53 on Windows, `pack.threads=1`); the same procedure
first rebuilt the 4.3.31 bundle byte for byte. Only one public root is
advertised. The sandbox image gains the Node.js runtime; OpenClaw, plugin and
dependency versions, the base image digest, policies and trust anchors are
unchanged. Current source/bundle pins above identify this candidate; prior
sections remain historical evidence. No private source history or signing
credentials were used.

The candidate was rebuilt before publication with review fixes: bootstrap's
node proof is bounded at 120 seconds, removes its own named container and
fails with a fixed `Sandbox runtime proof failed:` message; the Node.js
download stops on a stalled connection; the e2e suite covers both; and the
qualification record names the macOS update's sandbox retirement and the new
functional source checkpoint. The public vendor tree is
`bcfd325ce22da8f859bfb580c21897e2abd81f29`. Two independent empty bare
repositories produced byte-identical bundles with the same procedure, identity,
timestamp and message (Git 2.53 on Windows, `pack.threads=1`); the same
procedure reproduced the earlier candidate's commit
`b966b349b6cb2863157f9b2793a90508ead6a08d` from its tree, though not its bytes,
since the packs the alternates name have changed. This replaces the unpublished
earlier candidate. Only one public root is advertised; versions, image digests,
policies and trust anchors are unchanged, and no private source history or
signing credentials were used.

## ODS-maintained 4.3.33 Courier dependency candidate

This candidate updates Web Courier's hash-locked certifi and urllib3. The
version advance preserves the strictly increasing source-upgrade gate;
`pixel/ODS-QUALIFICATION-4.3.33.md` records the isolated runtime validation
and installed upgrade qualification still required. Existing compatibility
records and all source custody checks remain intact.

The public vendor tree is `15b6fb0cecb6978624aa96014f5ebccdb591a2fa` at ODS source
commit `f76c335d9b9e9ce87980ce909ede4e3ec368dcbb`. Two independent empty bare repositories
using the alternates procedure above produced byte-identical bundles with
the retained public synthetic author, timestamp and root message. The
bundle advertises one root commit and no private history. Current pins
above identify this candidate. The prior 4.3.32 bundle commit
`4bf2203c56cce323490e41e36736062d4f74cf39` and digest
`0f45a292d978bd766d19606404554a0ebdca6e1f8e0fb95d870e2f7d3ceded5b` remain historical identities in Git
and in existing clients' retained receipts. No release signature was generated.

## ODS-maintained 4.3.34 Source Broker readiness candidate

This candidate prepares the Source Broker for owner-connected Google accounts:
Calendar pagination and window coverage, per-source refresh isolation with a
content-free `status.json`, granted-scope handling, a broker-identity sign-in
exchange and revocation, and a `--prepare` install mode. ODS keeps every source
limb off, so ODS installations run none of the new broker code and render the
same `openclaw.json`. `pixel/ODS-QUALIFICATION-4.3.34.md` records the automated
evidence and the installed upgrade qualification still required. The version
advance preserves the strictly increasing source-upgrade gate.

The public vendor tree is `18d78dbf9770cc1a639f8a4157621790eaf69b1d` at ODS source
commit `05bf656517db1093d67e3970cafc5b42bf18b7bc`. Two independent empty bare repositories using the alternates
procedure above produced byte-identical bundles with the retained public
synthetic author, timestamp and root message (Git 2.53 on Windows,
`pack.threads=1`); the same procedure first reproduced the 4.3.33 commit
`3d2acd8f22a53d28b36b7e6ee017bb3143dc428b` from its tree, though not its bytes,
since the packs the alternates name have changed. The bundle advertises one root
commit and no private history. Current pins above identify this candidate. The
prior 4.3.33 bundle commit `3d2acd8f22a53d28b36b7e6ee017bb3143dc428b` and digest
`f136282398bdcabe5590af360e5bdce6e0e89ae28e29e8376d3bd7f8a05266e9` remain historical
identities in Git and in existing clients' retained receipts. No release
signature was generated and no private source history or signing credentials
were used.

The candidate was rebuilt before publication with independent-review fixes: a
disconnected sign-in clears the last snapshot, Calendar busy checks match overlapping
events, revocation always deletes the stored sign-in, restore refreshes before it
verifies, `--confirm` keeps a newer sign-in already in custody, and the control UI chat
inherits the new flags. The functional source checkpoint is
`12225ccb390c84f4a08e60cfc25e966e6e45f08f`. The same procedure produced byte-identical
bundles twice. This replaces the unpublished earlier candidate commit
`0684f0791dcb56f9473d5fe4b06f9f22f028f74a`.

It was rebuilt again for the owner connect flow: every release now carries the
Source Broker plugin's locked `typebox` dependency, so a source turned on after
install loads the plugin from the active release, and the sign-in exchange records
only the account that Google's ID token verifies. The functional source checkpoint
is now `096eba7f81d4bbbd588863b367fac343ecf6c86f`. Two independent builds were
byte-identical. This replaces the unpublished candidate commit
`cb3fe963dec251e2d2ae7639d5424bda2ddbfd85`.

It was rebuilt once more so `pixel verify` accepts a broker whose first refresh
after a source is turned on is still running. The functional source checkpoint is
now `9a50d0522cbf97cc9fa1991c431ea8e3ef74238a`. Two independent builds were
byte-identical. This replaces the unpublished candidate commit
`9942dbb6f85b90dea02c5c490274f29bc3ef81ee`.

## ODS-maintained 4.3.35 Gmail drafts candidate

This candidate adds Gmail drafts the owner approves: a proposal-only
`pixel_gmail_draft_propose` tool behind the answers-file key `emailDraftsEnabled`
(off by default), and a broker actuator that creates exactly the approved,
hash-bound snapshot in the owner's Drafts folder, once. A Message-ID fixed before
any Gmail call lets reconciliation find an unconfirmed draft instead of creating
another. There is no send tool. With the key left out, drafts stay off and the
rendered configuration denies the new tool like every other inactive source tool.
`pixel/ODS-QUALIFICATION-4.3.35.md` records the automated evidence and the
installed upgrade qualification still required. The version advance preserves the
strictly increasing source-upgrade gate.

The public vendor tree is `e3f5d13ce68c9a0b2baf8b71b841a18dd82a7ffa`. The
functional source checkpoint is `0194de8a3e81fc5e27f20d87c836956a5587c1d6`. Two
independent empty bare repositories using the alternates procedure above produced
byte-identical bundles with the retained public synthetic author, timestamp and
root message (Git on Windows, `pack.threads=1`); the same procedure first rebuilt
the 4.3.34 bundle from its tree `18d78dbf9770cc1a639f8a4157621790eaf69b1d` byte
for byte. The bundle advertises one root commit and no private history. Current
pins above identify this candidate. The prior 4.3.34 bundle commit
`8cae7723c0098f7c8b39bdf52a54b2f3a553e97d` and digest
`4124cb5e940123c2ee1a9d0cb48664341058fb2b91359ada7f99c43c6e9f585f` remain historical
identities in Git and in existing clients' retained receipts. On Windows,
`scripts/verify-pixel-bundle.py` needs `core.autocrlf=false` for its temporary
clone (for example `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.autocrlf
GIT_CONFIG_VALUE_0=false`), because Git for Windows checks text out with CRLF by
default. No release signature was generated and no private source history or
signing credentials were used.

## ODS-maintained 4.3.36 native MCP preservation candidate

This local candidate preserves the native OpenClaw MCP registry and explicit
tool permissions when Pixel renders an updated configuration. It does not add
another MCP implementation or OAuth store. The macOS preparation path renders
against a private copy of the selected previous state and verifies that the
registry is unchanged before activation. The functional checkpoint is
`2ac52729f`; see
[`pixel/ODS-QUALIFICATION-4.3.36.md`](pixel/ODS-QUALIFICATION-4.3.36.md) for evidence
and the remaining acceptance gates. This is not a claim of fleet acceptance.

The public source tree is `fe3f735f790280532b6c2229ce8b9b7fcf8621ce`.
Two independent empty bare repositories, using the documented alternates
procedure and `pack.threads=1`, produced byte-identical bundles with the retained
public synthetic identity, timestamp and message. Recreating the previous tree
also reproduced its exact synthetic commit. Only the new root commit is
advertised; no private Git history, signing key or release signature is included.
The current artifact is identified at the top of this document. The previous
4.3.35 bundle commit `4e176a0d158166bad230e62c4e0d81501ca0e1b4` and digest
`8300883de8b9e2bfd6c67d8baaddc10b8671873521dbaa794ffea6060ef4b1d2`
remain historical identities in Git and existing clients' retained receipts.

The renderer/overlay and live-planner cross-check passed through email/wiki
connection changes with native MCP retained. A real pinned gateway test after
re-render preserved an allowed read and refused an excluded write. Native Mac
runtime/upgrade qualification, complete owner-account flows and the direct
release-contract check remain as documented in the candidate record.

The earlier unpublished bundle `dbf6af6e9d6758a72c30585b3baab7ee4222c3bd`
passed the existing isolated Pixel end-to-end suite on Linux and the source-upgrade
and dashboard reconciliation suites. Its Docker/systemd boundaries are controlled;
these checks do not constitute native Mac or complete installed fleet qualification.
The candidate subsequently corrected the native MCP fixture evidence in the
qualification note, then added the declared-required-argument guard and its
regressions at the functional checkpoint above. It replaces the unpublished
artifacts and does not inherit a claim that the exact new bundle ran those
suites. The then-current `57608788b03da813172323f2ab4caa463c72c63d` bundle
checkout subsequently passed that isolated end-to-end suite in 89.20 seconds.
The ODS package with all installer
repairs also passed the corrected loaded-Pixel native MCP read/refused-write
case in 30.15 seconds. Neither result qualifies real-model inference, owner
OAuth consent or native Mac behavior. The comparison-fixture release gate described in
the candidate record remains open and also fails on the untouched 4.3.35 bundle.

The current source also derives Calendar start/end labels at read time from
source timestamps, explicit timezones or UTC offsets. It supplies the weekday,
handles offset date changes and repeated daylight-saving hours, and identifies
exclusive all-day ends without changing source timestamps or persisted data.
Invalid or floating timestamps and invalid timezones receive no invented label.
The existing Source Broker plugin suite covers these cases, including execution
with a different host timezone. Calendar interval schemas describe full timestamp
bounds. No tool permission, custody or provider action changes with these labels.

The earlier `449c05965b0b53f0f335fd719693caa860e05406` exact bundle checkout
passed the isolated Linux end-to-end suite in 110.54 seconds. Both independently
built artifacts matched, and source/commit/digest/executable-mode verification
passed. The local 2B/8K experiments used an unpromoted compact operating context
and read-tool surface; accurate Calendar answers there do not establish complete
prompt-budget or model-reliability acceptance. The provider trace still shows
output-budget clamping after catalog searches. Those experiments are not claimed
as production behavior or as a completed model-quality gate.

The current candidate additionally validates Calendar query intervals. A bound
must name a real date and an explicit Z/UTC offset; floating times, date-only
bounds, unknown offsets, malformed dates and reversed/empty intervals return
an actionable error instead of using the machine timezone or widening the
query. Equivalent offset/fractional timestamps retain the same events and
coverage. Source timestamps, stored projections and tool permissions are
unchanged. Both new plugin regressions failed on the preceding source and pass
with this change, including under a different host timezone. The older bundle's
end-to-end result above does not qualify this new artifact.

The current exact bundle checkout passed the same isolated Linux end-to-end
suite in 79.32 seconds. Independent builds matched byte for byte, and the
source/commit/digest/executable-mode verifier passed. These checks retain the
controlled Docker/systemd, real-account, native Mac and model-reliability limits
documented above; no fleet or release acceptance is implied.

## ODS-maintained 4.3.37 incremental Google sync candidate

Functional source checkpoint `da05e4ddbb9bdefdecc154614c4d7ec33b2d9a3a`
adds resumed Gmail/Calendar batches and status publication before historical
scanning finishes. It retains explicit partial coverage, private cursors,
credential/query/sanitizer bindings and Sent body restrictions. The ordinary
held source upgrade requires increasing the candidate version from 4.3.36.
Dependency/image pins, OpenClaw and signing trust anchors are unchanged.
See [the candidate record](pixel/ODS-QUALIFICATION-4.3.37.md) for exact scope.

The source tree is `3c1d7fdbda78ef7c7f7962d81d81762f448c0734`. Two independent
single-root builds produced commit `f3ce6159e5608e582467c4a3e87b7052b62f7b5e`
and SHA-256 `a0dcd0e6923960d4f4fbed2cca8dc4737af6e37d8e4a5eb3ac6b471ea9feb723`,
matching byte for byte. The exact source/digest/Git-mode verifier passed. The
final exact Git bundle checkout passed the existing Linux clean-room
plan/apply/verify suite in 86.30 seconds with controlled Docker/systemd.

The functional broker cohort passed 114 cases, including real loopback
slow-response controls; source-upgrade/provider/Mac-bootstrap checks passed
465 cases with two old-version expectations subsequently corrected and all
90 bootstrap cases passing. Those bootstrap cases run on Linux and do not
qualify native Mac execution. Root reconnect tests separately exercise the
quiesced producer transition, not complete isolation of an active conversation's
write intent across an account switch. Installed, provider, Mac, fleet and the
preexisting comparison-fixture release gate remain separate acceptance work.
