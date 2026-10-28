# Portal integrations: consolidation draft

This is a development checkpoint for ODS main, not release acceptance. The
combined branch includes the existing connected-apps stack (#8196 through
#8223 and the runtime-readiness fix #8226), plus scheduled tasks and native
integration work. The original PRs remain separate; this document does not
mark them merged or replace their review.

## Implemented

- Integrated the existing Google connection wizard, email/calendar reads,
  reviewed calendar changes, Gmail drafts and Knowledge wiki with the Portal
  branch. The Google Source Broker keeps its sign-in; drafts require approval
  and Portal cannot send email.
- Added Scheduled tasks in the workspace using OpenClaw's existing cron
  service, with owner-authenticated controls, pause/resume and saved results.
- Qualified runtime plugin imports before activation; manifest visibility
  alone no longer counts as a working plugin.
- Preserved native MCP servers and tool restrictions through Pixel rendering
  and macOS configuration staging, with a reproducible Portal runtime source
  candidate/public bundle using internal package version 4.3.39.
- Added an owner-side, version/hash-bound adapter to OpenClaw's MCP OAuth,
  retaining its discovery, registration, PKCE, token storage and refresh.
- Added internal Root consent intent/callback claiming, an independently bound
  owner operation and safe recovery of an exchange whose reply was lost. It
  preserves native credentials and does not infer Portal access from them.
- Improved source argument feedback, explicit calendar timestamps and source
  labels; kept permitted email/calendar read schemas available in the 8K
  Portal profile and bounded native tool-discovery/compaction metadata.
- Corrected connection recovery across gateway reloads. Permission changes
  use the existing Root/Edge holds and runtime proof; removal prevents fresh
  source reads while preserving conversation delivery and completed results.
- Retained ODS's existing workspace design and removed idle gray outlines
  from the requested surfaces; keyboard focus remains visible.
- Completed native direct-tool admission after execution, including errors.
  The exact pinned SDK repair uses its existing after-tool hook. Concurrent
  direct calls sharing an identity remain busy until both finish; an embedded
  run cannot complete another run's tool admission.
- Validated Pixel's deferred tool arguments with OpenClaw's own schema
  validator before dispatch. Missing wiki arguments now identify the required
  field instead of reaching the plugin as an undefined value; native coercion,
  permission filtering and other agents' dispatch behavior are retained.
- Added a current-request-only guide for connected reads, distinguishing local
  wiki and app records from public web search. It loads beside the owner turn;
  the reusable 8K, compact and full operating cores retain their existing size.
  This guidance is not an access grant or a source-verification receipt.
- Added strict declared-schema validation before Source projection reads and
  proposal creation. Unknown parameters such as `max` refuse with a correctable
  schema instead of silently selecting an omitted `maxResults` default. Actual
  locked TypeBox and native SDK controls qualify that refusal and corrected call;
  valid omitted arguments keep their existing defaults. Prompt limits remain
  distinct from enforced permissions and per-call schema validation.
- Added an internal reviewed Notion read contract, disabled preparation and
  protected app choices shared by the live owner transaction and installer.
  Actual Pixel qualification caught and corrected broad implicit MCP access
  from the coding profile. Google disconnection preserves independent native
  choices; source-overlay proof derives those choices from Root's record.
  [The groundwork review](pixel/native-mcp-apps.md) records the permission limits;
  the public journey below composes it with effective-access readiness.
- Added a bound native readiness check for the configured default Portal model,
  with real native policy evaluation and no account-content tool call. Root
  requires fresh runtime/configuration proof; local credential cleanup requires
  a verified disabled runtime and reports no provider revocation. A durable
  authorization reservation prevents two activations from consuming one flow.
  The internal enrollment controller now joins preparation, bound consent,
  durable activation, recovery and verified withdrawal. Exact step IDs and
  before/after file hashes prevent a lost reply from replaying a grant. Other
  Google/plugin mutations respect its pending state under the existing lock.
  Model/provider/settings/source writers now also respect pending enrollment.
- Added the complete authenticated reviewed-app host/API/Settings journey,
  starting with Notion search and read. The public catalog is fixed; the screen
  discloses broader provider consent, connected-source reach and sharing with
  the selected model provider. Signed-in is separate from verified Portal access;
  uncertain outcomes retain Finish/withdrawal and local forgetting never claims
  provider revocation. Existing grants can use Check access without inventing
  another consent flow. Readiness survives ordinary chat/cron gate epochs, but
  refuses unsafe gates or changed policy/runtime bindings; interrupted checks
  cannot reuse an old positive result. Controlled full API/host/Node/Unix journeys
  exercise the existing Root/owner transaction. Native Mac, real Notion and installed
  update/rollback acceptance remain open. Coffee's custom MCP/app work stays
  separate; [the public contract](PORTAL-NATIVE-APPS.md#authenticated-reviewed-app-journey)
  records the boundaries.

## Qualification boundaries

The ordinary Google connection is designed as **Connect Google → choose account
→ authorize**, without a per-user Cloud project or client JSON. Protected Root
configuration, authenticated host/API transport and Settings now carry that
journey; owner-provided clients remain explicit Advanced setup. Official app
registration/verification and reviewed release provisioning are still required.
A personal Desktop client completed real read-only consent locally. The installed
Portal discovered the tools, read one sanitized email projection and queried a
covered seven-day Calendar interval with zero events. The inbox lookup's persisted
preview is truncated, so this does not prove that only one email summary reached
the model. A subsequent one-time task created in the installed UI performed
bounded Gmail/Calendar reads through native cron and retained its result in
Past tasks after schedule removal. It disclosed the partial email index but
confused pagination with synchronization coverage. This scoped scheduled read
does not qualify arbitrary model answers, real refresh or withdrawal. That
client is not the official ODS application and must not be distributed as one. See
[the managed Google contract](PORTAL-GOOGLE-APP.md).

The real first sync exposed Gmail history blocking Calendar and status. The
4.3.37 candidate adds resumed, independently bounded Gmail/Calendar batches,
explicit partial coverage and deadline/size-aware HTTP reads. Root reconnect
quiesces prior producers and preserves old actions privately before a token can
change. The ordinary installed upgrade to this candidate completed: the Google
grant was unchanged, the owner/source upgrade reached ready/complete, the runtime
was verified and the four core containers were healthy. Gmail continued growing
its explicitly partial index while Calendar reported complete coverage of its
configured window. Source-change and reconnect crash controls still use fixtures.
The development branch now holds both Edge and native admission throughout a
Google exchange and verified owner apply. Finish covers interrupted exchange,
gateway restart and interrupted release without replaying OAuth or the committed
owner write. Controlled crash lanes qualify these changes; the complete
installed account-change/write-workflow journey remains open.

The ordinary installed upgrade at `2486daa6a` subsequently applied internal
package 4.3.38/source `6b7154c88ecbbffed61c2984a5bc986915a1859e`. Runtime and
source state returned verified/complete, the four core containers were healthy,
the original read-only Google grant and model configuration were preserved, and
temporary QA delegation was removed. Gmail results now distinguish indexed
matches from unknown whole-mailbox totals with explicit coverage metadata.
One installed 9B UI request discovered/described Inbox and Calendar, requested
one email summary and exactly seven days, and completed both calls without an
execution error or fallback. The answer identified the partial index and
explicitly disclaimed a whole-account total. Gmail's persisted result details
were truncated, so this trace does not independently prove the number of
returned summaries or every coverage field. Calendar returned zero events with
fresh, complete coverage of the requested interval. This is one observed
model request, not a general answer-quality guarantee. The later Google
admission and public Native-app blocks are not included in this installed
checkpoint.

A second one-time Google-count task ran in native cron after the Scheduled
tasks panel had been closed. It completed Gmail and an exact seven-day Calendar
query, reported the partial local email index rather than a mailbox total, and
kept its result in Past tasks after normal removal. No sender, subject or email
address appeared in the final answer. The browser itself remained open. The
trace also exposed a failed instruction: the model used `max` instead of the
schema's `maxResults` and skipped schema description. That unknown optional
field did not prevent execution, so the one-summary limit is not qualified;
the persisted Gmail result was truncated. This case remains a negative
argument/bounded-read acceptance result even though the scheduled execution
and final counts presentation succeeded.

The subsequent 4.3.39 Source change closes this argument defect in controlled
plugin and actual pinned-dispatcher tests: the incorrect `max` refuses before
projection access, and `maxResults: 1` reads one synthetic record. Five new
regressions fail against the previous code. This does not yet establish that an
installed model will make or repair the intended bounded call; the earlier
scheduled failure remains part of the acceptance record.

Local disposable WSL/Linux environments exercised the pinned ODS OpenClaw,
real plugin loading and tools, the owner worker, Root transactions, native
cron, Docker Edge, WSL Unix sockets, ingress and saved conversation delivery.
The joined permission lifecycle holds both admission gates before a write,
restarts and proves the actual runtime, then withdraws source permissions
without losing completed work. Separate authenticated API/Root and controlled
Source Broker service lanes have also run; they are not a single installed
account-to-browser acceptance run.

The MCP runtime test uses two configured native servers with one exact tool
grant: the permitted read succeeds while the filtered write and the unrelated
server's read remain undiscoverable and uncallable. OAuth uses a controlled
provider and the actual native store, callback listener, PKCE exchange and
refresh. No real external account was connected by this qualification.
The reviewed managed-contract variant also exercises its real grant transform
and ordinary core reads. Disabled-server OAuth retains no discoverable tools
before activation. These are controlled-provider/private-runtime lanes, not
installed Notion account acceptance.

A temporary CPU model answered fresh synthetic email/calendar questions
through the actual 8K rendered profile and Edge. This is scoped evidence;
small-model reliability, including natural-language scheduled briefings, is
still open. The temporary engine is stopped. No permanent hardware preference
was set by that CPU qualification.

The owner's installed RTX 5090 is now visible in Windows, WSL and Docker. The
existing Qwen3.5-9B Q4_K_M runs at 64K with all model layers on the GPU. Actual
Portal chat returned the correct synthetic task total, and a one-time task
created in the owner UI completed in native cron while another screen was
open. Its result remained in Past tasks after removal. Actual chat also created
and read a fictitious Knowledge wiki note through its native tools; disabling
the plugin retained every Markdown page. These local results do not qualify a
real Google account, all models or other hardware.

An additional direct HTTP wiki read exposed a pinned SDK omission: execution
finished without the after-tool hook and left a phantom active tool. The
reviewed repair is covered against the actual pinned package for success,
execution failure, admission refusal and overlapping calls with the same ID.
Installation uses the existing byte-bound repair/restore mechanism, including
macOS staging; that staging evidence does not claim native Mac execution.

An installed post-update wiki check also exposed a model-quality failure: a
chat fell back to a file read after malformed wiki arguments, and one scheduled
answer invented facts without making a read. Neither counts as successful
connected-read acceptance. A private real-5090/native-cron reproduction then
isolated the deferred argument-validation defect; the corrected private runtime
read the randomly marked fixture note correctly. A full-toolset installed UI
follow-up still chose public web search instead of the local wiki. After the
scoped connected-read guide was installed at `508cac985`, the same UI query
used native wiki discovery, schema description and a successful `wiki_get`,
returned the actual marker/time and used no web, shell or file-read fallback.
A one-time task then created through the installed owner UI ran in native cron
while Settings was open. It discovered/described the wiki, successfully called
`wiki_get`, returned the actual marker/time and preserved every Markdown page,
with no web, shell or file-read fallback. These scoped positive checks do not
erase the earlier failures or qualify arbitrary model requests. Reliable
truthful completion when the model does not call a source at all remains open.
Scheduler execution success alone does not verify the content of its answer.

Real Google chat exposed two further limits. A technical Portuguese request
selected the extension catalog and Operations inventory instead of account reads;
that does not count as a connected read. A natural request did use Gmail and
Calendar, recovering a wrong `id` argument with the required `messageId`, but
repeated email details despite a counts-only instruction. No mail or event was
changed. App-specific current-turn guidance and presentation instructions are
being qualified; model instructions are not privacy enforcement or proof that
arbitrary requests will choose the right source.

Representative repository checks (use the pinned package/dependency staging
from `portal-runtime.yml` for the native tests):

```sh
python3 -m pytest -q ods/extensions/services/pixel-agent/tests/test_native_mcp_runtime.py
python3 -m pytest -q ods/extensions/services/pixel-agent/tests/test_native_mcp_oauth.py
python3 -m pytest -q ods/extensions/services/pixel-agent/tests/test_access_native_origin.py ods/extensions/services/pixel-agent/tests/test_connection_coordinator.py
```

The working qualification notebooks live outside the source tree. Their
private fixture paths and synthetic results are not shipped as user data.
Remote CI for the consolidation must be assessed at the actual PR head;
earlier checks on #8226 do not certify this branch.

## Still required

- Qualify the installed reviewed-app journey with an explicitly authorized real
  provider, including Windows callbacks, existing connections and update/rollback.
  The authenticated public wiring and controlled HTTP/Root lanes are implemented;
  they do not certify a real Notion account or native Mac execution.
- One complete installed UI journey for connection, chat, background result,
  permission withdrawal, update and rollback, beyond the component lanes.
- Real-provider refresh and disconnect; real Calendar and draft actions;
  honest distinctions between saved, signed in, reachable and usable by Portal.
  Token presence alone is insufficient. Read-only consent and the scoped reads
  above do not establish these other lifecycles.
- Native macOS execution and the applicable release/fleet lanes. The exact
  reviewed Codex comparison spacing/checksum is restored without changing its
  expected pin. The full upstream release contract still requires historical
  audit documents deliberately excluded from the public export; it is not a
  passing public-release qualification.
- Natural-language scheduled-task reliability on limited models, and broader
  model/hardware validation beyond the scoped local 5090 results above.

## Coordination with Coffee

Coffee owns custom MCP server setup by URL/local command, tool inspection and
Off/Ask first/Allowed permissions, Home Assistant, ntfy notifications, Obsidian,
their exact-action approvals and their separate integration service/Settings
section. His reported scope also includes owner/agent transport separation,
installer/uninstaller handling and controlled end-to-end tests. These are
reported work items awaiting his PR, not code or security results validated by
this branch.

This branch supplies Google/email/Calendar, reviewed fixed-app connection,
native runtime lifecycle and native cron foundations. It does not add a
competing custom-MCP editor, notification service, vault connector or approval
service. The Knowledge wiki's local Markdown storage is distinct from Obsidian.

When Coffee's PR is available, review the shared Settings navigation, native
registry/server IDs, preserved grants, pending Root/source transitions and
current-model scope. Scheduled tasks should invoke his notification tool through
the existing native cron/permission path. Agreement on those seams requires the
actual code; this document does not claim the two implementations already
compose successfully.

Further native MCP design and exact remaining wiring are documented in
[Portal native apps](PORTAL-NATIVE-APPS.md). Keep this consolidation in draft
until those acceptance boundaries and maintainer review are resolved.
