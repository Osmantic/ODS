# Assistant display name

Settings → Assistant identity changes the name shown in the assistant navigation,
chat heading, composer, current working label and preview label. The default is
**Assistant**. Enter a name and choose **Save name**. Blank names reset to
Assistant; **Reset to Assistant** edits the draft and still requires Save.

Names are saved for this ODS installation, not in a browser profile. Reloading
the dashboard or opening it in another browser reads the server's saved name.
An already-open browser can use **Refresh saved name** to observe another editor's
change. The human profile and assistant identity are separate.

This is display text, not a system prompt, account identity, permission grant or
model setting. Existing chat/model content is not rewritten. Internal `pixel`
service, plugin, route, session and approval-receipt identifiers stay unchanged.
Saving a name does not apply runtime preferences or restart inference.

Names are normalized to NFC, trimmed, and limited to 60 Unicode code points.
Control characters and ambiguous formatting controls are rejected; ordinary
international names and emoji joiners are supported. Names render as text,
never as HTML. The authenticated owner API rejects unknown fields and oversized
requests and does not expose internal errors.

Persistence uses the existing private provider-store custody and locking at
`DATA_DIR/pixel-providers/portal-identity.json`, independently revisioned from
`pixel-settings.json` and `provider-config.json`. Older settings readers therefore
retain their previous schema during rollback. Do not manually change private
store permissions to make an identity error disappear.

Each Save requires the last observed revision. Conflicts or an unconfirmed
response disable another Save until an explicit refresh. The dashboard confirms
both the Save response and a matching subsequent server read before displaying
success; it never automatically replays a Save. If data is corrupt, it is not
silently replaced with a default. An unavailable initial read displays Assistant
as a fallback label and keeps editing disabled with an error.

## Saved-name preservation

Every valid saved display name, including `Portal`, remains unchanged across an
upgrade. ODS cannot tell whether an existing value was accepted as an earlier
default or deliberately chosen, so only an owner edit or reset changes it.

## Presentation-only semantics

The display name is cosmetic. It does not change the structural agent ID
`pixel`, plugin ID `pixel-ods`, internal services, routes, session identifiers,
model selection, permissions, tool access or runtime configuration. It is not
inserted into the privileged model prompt, and it does not rewrite prior chat
messages or workspace content.

## Ordinary conversation and runtime disclosure

The assistant presents itself generically as the owner's ODS assistant. It does
not volunteer or promote framework, vendor, implementation or internal-service
names in ordinary replies. When the owner explicitly asks about the runtime or
when an implementation detail is material to accurate troubleshooting, it
answers truthfully from available evidence. Administrator diagnostics, logs,
licenses and migration documentation retain exact technical names, including
OpenClaw when that is the active runtime.

## Qualification boundaries

Contract, component and disposable host/API tests are source evidence. Release
acceptance additionally requires an actual installed Settings save, visible
navigation/chat readback, reload and independent-browser persistence, conflicting
edit handling, restoration of the previous name, and unchanged runtime/settings
and chat history. Source test success alone does not establish these journeys.
