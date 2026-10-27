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
or installed model configuration was changed.

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
  model/hardware validation. No GPU result is claimed.

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
