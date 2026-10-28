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
  and macOS configuration staging, with a reproducible Pixel 4.3.36 source
  candidate and public bundle.
- Added an owner-side, version/hash-bound adapter to OpenClaw's MCP OAuth,
  retaining its discovery, registration, PKCE, token storage and refresh.
- Added internal Root consent intent/callback claiming, an independently bound
  owner operation and safe recovery of an exchange whose reply was lost. It
  preserves native credentials and does not claim Portal access or expose a
  public enrollment operation yet.
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

## Qualification boundaries

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

- Root-managed native app enrollment: exact owner/config/runtime/state binding,
  a durable callback claim and crash recovery, activation and withdrawal under
  the existing owner transaction, and authenticated Settings/host routes.
- One complete installed UI journey for connection, chat, background result,
  permission withdrawal, update and rollback, beyond the component lanes.
- Authorized real-provider consent, refresh and disconnect; real Calendar and
  draft actions; honest distinctions between saved, signed in, reachable and
  usable by Portal. Token presence alone is insufficient.
- Native macOS execution and the applicable release/fleet lanes. A preexisting
  Codex comparison-file checksum still blocks claiming the Pixel release gate
  passes; it also reproduces with the previous untouched bundle.
- Natural-language scheduled-task reliability on limited models, and broader
  model/hardware validation beyond the scoped local 5090 results above.

## Coordination with Coffee

Coffee owns the custom MCP server setup screen, Home Assistant, ntfy
notifications and Obsidian integration. Those are his stated work items, not
features claimed complete here. This branch supplies shared native connection,
permission and lifecycle foundations; it does not add a competing custom-MCP
setup UI. The Knowledge wiki's local Markdown storage is distinct from
Coffee's requested Obsidian connector.

Further native MCP design and exact remaining wiring are documented in
[Portal native apps](PORTAL-NATIVE-APPS.md). Keep this consolidation in draft
until those acceptance boundaries and maintainer review are resolved.
