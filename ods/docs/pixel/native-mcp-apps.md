# Reviewed native MCP app groundwork

This is an internal configuration and consent foundation in the consolidation
draft, not a released Notion connection or a new custom MCP setup screen.
Coffee owns custom MCP setup, Home Assistant, ntfy and Obsidian.

The reviewed candidate uses Notion's `https://mcp.notion.com/mcp` endpoint with
OpenClaw's native OAuth client, credential store and refresh. Its exact raw tool
filter contains `notion-search` and `notion-fetch`. Notion may expose content
from other sources connected to that account; these two tool names do not mean
Notion-only data scope. Read content enters the conversation and its selected
model provider. Local disconnection and provider-side revocation are separate.
See Notion's [connection guide](https://developers.notion.com/guides/mcp/get-started-with-mcp)
and [security guidance](https://developers.notion.com/guides/mcp/mcp-security-best-practices).

## Configuration contract

- The protected connection record can retain boolean `nativeApps` choices.
  Known choices become exact `nativeMcpApps` onboarding entries. Older records,
  targets and owner answer frames remain readable; unknown future IDs are kept
  in the record but cannot grant access in this release.
- Preparation keeps `ods-notion` disabled. Native OAuth can authorize this
  disabled entry without exposing its tools. The bound consent journal still
  reports `portalAccessVerified: false`; tokens are not readiness proof.
  Preparing a disabled entry does not replace the existing agent allowlist.
- Configuration changes use the existing Root/owner connection transaction and
  restart gates. Its before-images cover configuration, environment and
  onboarding. Failed activation restores those files; an unknown result remains
  held for recovery. No credential or receipt reset is introduced.
- Activation grants only the two qualified names. The pinned native coding
  profile also includes `bundle-mcp`, and `alsoAllow` is additive. Therefore a
  new agent allowlist restricts implicit MCP access while retaining core groups,
  the reviewed ODS plugin families and existing explicit global grants. Native
  profile, provider, sandbox and deny layers still apply. A preexisting explicit
  agent allowlist is preserved and can prevent activation. Before writing, the
  owner transaction qualifies the staged configuration through the pinned
  native namespace, materializer and effective policy, using the configured
  default model and actual sandbox mode. It projects only inventory tools,
  without contacting a provider or reading credentials. A refusal leaves all
  three owner files and previous completion intact. Static eligibility still
  requires a live readiness check after activation; it is not a connection claim.
- Existing additive agent policies require review before activation. Registry
  keys that need native sanitization also require review, preventing another
  server from borrowing a managed app's qualified names. Neither is silently
  migrated or renamed. Disabling the managed entry remains possible.
- Disable retains a disabled native entry and withdraws its exact grants.
  An agent policy narrowed at activation stays narrowed when the app is disabled;
  disconnect must not restore implicit broad MCP access.
  Disconnecting Google retains native app choices, including disabled choices.
  The installer overlay and live planner share an identical transformation.

The core groups and plugin families above are not a grant for every plugin:
`bundle-mcp` and `group:plugins` are never added by this transformation. An
existing explicit broad owner grant remains an explicit broad owner grant.

## Qualification and remaining work

Disposable WSL tests exercise onboarding, exact contracts, name collisions,
the installed Root package, owner-file rollback, busy-runtime refusal and
Google disconnection independently of native apps. Pinned native-runtime tests
exercise OAuth with a disabled server and controlled provider, plus actual
Pixel turns attempting an ungranted server, a filtered deletion, a permitted
read and an ordinary core read. Fixtures never use a real Notion account.

The internal `native_enrollment.Controller` now joins disabled preparation,
consent, durable authorization claims, activation, recovery and withdrawal to
the existing gated owner transaction. Each step binds its exact Root choices,
before/after config, environment and onboarding hashes, transaction ID and owner
state. Recovered receipts are verified against live files and fresh runtime
proof; cancellation/withdrawal cannot submit a previously unstarted grant.
Known unclaimed consent expiry closes without erasing valid native credentials.
Readiness is scoped to `pixel-owner-default` and cached for at most five minutes,
with fresh configuration/runtime proof and no provider contact on status polls.
Google/plugin mutations honor a pending native flow inside their existing lock.

Still required: authenticated host/API routes and the Settings journey,
coordination with model/provider/settings/source mutations, an authorized real
provider and installed update/rollback, including macOS.
No public Connect button is exposed by this groundwork, and this draft is not
ready for merge or release acceptance.
