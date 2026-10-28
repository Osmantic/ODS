# Portal and native OpenClaw app connections

ODS uses the MCP registry, OAuth implementation and private credential store in
its pinned OpenClaw package. It does not implement another MCP client, credential
vault or scheduler. The local implementation is still in preparation; there is
no new public native-app enrollment endpoint or catalog item yet.

The combined branch's implemented work, remaining gates and coordination with
Coffee are recorded in [Portal integrations status](PORTAL-INTEGRATIONS-STATUS.md).

## Implemented seams

Pixel's renderer retains native `mcp` configuration and the owner's existing
tool restrictions. The macOS preparer uses a private disposable copy of previous
configuration and checks registry preservation before activation. The ODS
4.3.36 source candidate records that change and its remaining release gates.

`extensions/services/pixel-agent/host/native_mcp_oauth.mjs` delegates these fixed
owner-side operations to OpenClaw:

| Operation | Native result | Meaning |
| --- | --- | --- |
| `status` | Credential-presence booleans | Saved material exists; this does not prove a live connection or Portal access. |
| `begin` | `redirect` with authorization URL, or `authorized` | OpenClaw either starts consent or refreshes an existing sign-in. An existing sign-in is not a new account selection. |
| `complete` | `authorized` | OpenClaw exchanged the code. The custodian must validate and claim the callback first. |
| `forget` | `forgotten: true` | The native local credential file was removed. Provider consent and an active gateway's cached credentials are separate. |
| `probe` | Reachability, effective permission and a fixed reason, scoped to `pixel-owner-default` | Checks the exact filtered tools with native policy and the configured default model. It executes no content tool and does not certify every conversation/provider context. |
| `preflight` | Static policy eligibility, scoped to `pixel-owner-default` | The owner connection transaction checks a private staged candidate before writing. It opens no server and accesses no credentials; it does not prove connectivity. This is not a public worker operation. |

OpenClaw performs discovery, dynamic registration, PKCE, token storage and token
refresh. There is no public MCP OAuth SDK export at version `2026.6.33`, so this
small adapter refuses other versions or a changed compiled module hash. It uses
the same native resolver, guarded HTTP fetch and origin-scoped headers as the
native CLI. It strips a configured resource Authorization header from OAuth
discovery requests. It never disables TLS verification or the native network
guard. A changed runtime needs a review of this seam, not a guessed import.

Inputs cross a bounded stdin frame, including the callback code. Outputs contain
only the selected result or fixed ODS errors; native exception bodies and token
values are not forwarded. Consent requires an explicit host loopback redirect,
so OpenClaw's legacy fixed-port fallback cannot redirect a Portal flow elsewhere.
The adapter neither edits the registry nor grants tools, restarts a service,
accepts HTTP requests or reads another account's state. It is currently exercised
by qualification and installed inside the protected Linux/WSL owner bundle,
not exposed as an independently callable public service.

`pixel_connections/native_mcp.py` now records Root's native consent intent under
the existing access lock. Both Root and `native_mcp_owner.py` independently bind
the owner, home, exact config/state directory, selected existing server, native
package/module, launcher and Node executable before work. The owner operation
uses the existing owner apply lock, refuses pending transactions and runs the
native adapter after the UID drop. Codes cross only its private pipe/stdin.

The journal is private, atomic and directory-fsynced before beginning consent
and before exchanging a callback code. It holds only metadata and hashes. The
full callback redirect and state must match the unexpired flow; duplicate
callbacks cannot start another exchange. If an exchange reply is lost, recovery
records an unknown outcome without replaying the code or inferring success
from credential presence. A subsequent explicit sign-in can reuse valid native
credentials, without claiming it selected a different account. Cancellation
retains existing credentials. A private guardian socket makes the native child
exit if its owner worker dies, preventing it from continuing an abandoned flow.

This internal seam currently selects an existing native registry entry. Its
`portalAccessVerified: false` result means this consent step has not proved
Portal access; it does not claim to withdraw permissions a server already had.
No public socket/API enrollment operation or reviewed app item is added yet.

The internal [reviewed app groundwork](pixel/native-mcp-apps.md) now carries a
fixed Notion read contract into the existing owner transaction and installer
overlay. It stages a disabled entry, preserves native choices when Google is
disconnected and supports restoring those choices from Root's protected record.
The pinned coding profile includes broad MCP access; adding `alsoAllow` alone
did not isolate the managed app in an actual Pixel turn. The new scoped agent
allowlist retains ordinary core/ODS tools and explicit owner grants without
adding `bundle-mcp` or `group:plugins`. Existing restrictive agent policies
remain effective. Ambiguous server-name aliases are refused before activation.
This internal contract is not a public Connect operation or effective-access
readiness proof.

The internal readiness operation now reads the exact bound configuration and
uses OpenClaw's native MCP materializer, effective policy pipeline and sandbox
resolver. These additional private SDK entry points are version/hash bound.
It contacts only the selected explicit HTTP server, without loading another
workspace/plugin MCP bundle. Missing saved tokens return `sign-in-required`
before any network request or client registration; token presence alone still
cannot produce a ready result. Available tokens may refresh through native OAuth.
The result distinguishes unavailable tools from policy blocks without returning
server launch parameters, descriptions, account content or credentials.
It projects the full native server namespace, including enabled plugin bundles,
and refuses a selected name changed by native sanitization before contacting any
server. The owner connection transaction now also qualifies the staged native
namespace and effective policy before granting tools. It projects the exact
reviewed names as native inventory-only tools, without contacting a provider,
registering a client or reading credentials. Failure leaves configuration,
environment, onboarding and the previous completion receipt intact. Withdrawal
does not require a new positive eligibility check; rollback uses the original
configuration validator. The public enrollment wiring and post-activation
readiness journey remain unfinished.

`check_access` joins that check to fresh Root runtime proof and rechecks both the
configuration binding and runtime revision afterward. `forget_disabled` also
requires the exact disabled configuration to have verified runtime proof; a
file edit without a verified restart is insufficient. The owner independently
checks the disabled entry before forgetting. Its result explicitly says
`providerRevoked: false`. These helpers remain internal; the managed controller
below composes them without exposing a public enrollment operation yet.

The consent journal can reserve an authorized, still-disabled entry for one
activation ID. That consumption is durable before any future controller grants
tools. Duplicate reservations, stale bindings and expiry are refused. A pending
reservation cannot be cancelled or replaced by a new sign-in; it must settle
against the same activation ID. Closing the claim is idempotent and still
reports `portalAccessVerified: false`. The internal enrollment controller now
persists its configuration plan before reserving and joins verified activation
or rollback to closing that claim; the journal phase alone never proves success.

`native_enrollment.Controller` owns a private, bounded and directory-fsynced
metadata journal. Its step binds Root records, before/after configuration,
environment/onboarding hashes and an exact transaction ID. A resumed owner
completion must match those files and fresh Root runtime proof. Withdrawal can
settle an existing attempt, but cannot freshly submit its old enable plan.
Only a verified disabled runtime permits local credential cleanup. Known consent
expiry before any claim closes without activating access or erasing valid
credentials. The default-owner readiness cache expires after five minutes and
also checks current configuration/runtime proof without Node/provider work on
status polls. Google/plugin mutations refuse a pending native flow inside the
same Root lock. Coordination with model/settings/provider/source writers and
the authenticated host/API/Settings journey remain release requirements.

Native diagnostics and the owner result now use separate channels: a bounded
anonymous private reply file carries the fixed result while SDK stdout/stderr
cannot corrupt the protocol or become public diagnostics. The private guardian
still stops the child if its owner worker dies.

## Actual runtime qualification

`test_native_mcp_oauth.py`, wired into `portal-runtime.yml`, uses the installed
pinned OpenClaw package with an isolated HOME/state directory and a controlled
loopback OAuth/MCP provider. It exercises the adapter and ODS's existing host
callback listener, rejects another flow's state before token exchange, checks
the native PKCE exchange, reads a fresh fact over authenticated HTTP MCP, and
forces a resource-side token rejection to prove native refresh in a fresh process.
The native store has directory/file modes 0700/0600; `forget` removes its credential file;
a subsequent fresh native runtime cannot connect without another sign-in.

Negative cases cover unreviewed runtime bytes, invalid requests/redirects and
remote errors that contain synthetic sensitive values. A discovered second
loopback origin is refused by OpenClaw's network guard before it receives a
request. The fixture must not weaken that guard to simulate cross-origin OAuth.
This does not qualify a public remote OAuth provider, provider-side revocation,
real Mac execution or the complete managed Portal enrollment journey.

`test_native_mcp_runtime.py` also configures two real native stdio servers and
grants the Pixel agent only one exact tool name, `qa-project__project_status`.
After Pixel configure/render and the ODS overlay, the actual agent can discover
and call that read. An excluded write and the other configured server's read
are absent from its catalog and refused when a controlled provider requests
them anyway. Only the granted read reaches either server. The fixture does not
grant the broad `bundle-mcp` group: an app's consent must not implicitly enable
other configured servers. This proves native permission isolation, not managed
app enrollment or a real provider login.

The managed-contract variant repeats these attempted calls using the actual
`ods-notion__notion-search` name and the production grant transformation,
through configure/render and the overlay. It also proves an ordinary deferred
core read still works. A separate native OAuth test completes consent while
the server remains disabled: saved tokens do not expose tools until activation.

Readiness tests additionally exercise native agent/global/provider/sandbox
denials, default-model scope, missing tools, resource-side token rejection and
native refresh, no registration when unsigned, unreviewed policy entry points,
no content reads and no requests to another configured server. Bound owner tests
reject configuration drift while `tools/list` is outstanding and require
disabled state before forgetting. Separate Root fixtures require fresh runtime
proof and reject a runtime revision change during the check. Reservation tests
cover concurrent claims, failed fsync, replay, expiry and same-ID settlement.

For the 8192-token primary Portal profile, native Tool Search keeps the schemas
of `pixel_gmail_search`, `pixel_gmail_read`, `pixel_calendar_list` and
`pixel_calendar_get` visible when those tools are already permitted and have
unique names. This lets a small model read their required arguments before its
first call. It retains the original tool objects, permissions and Source Broker
handlers; it cannot connect an account or restore a denied tool. Other tools,
including source writes, remain discoverable through the native catalog. The
smaller-context and larger-context surfaces keep their existing behavior.

## Wiring required before offering Connect

- Join the bound custodian/owner consent seam to a reviewed app selection and
  managed registry entry. Browser/model requests must not select package paths,
  launch commands or arbitrary server configuration. Qualify its intent/claim
  journal through the complete authenticated public/host route, beyond the
  real owner/native operation and callback tests.
- Reuse the host agent's loopback listener and owner-authenticated relay. On
  WSL the listener is on Windows while authorization runs under the bound Linux
  runtime. For a remote browser, validate the full pasted callback against its
  exact saved redirect and state. Never forward callback codes in argv or logs.
- Apply enrollment and tool permissions under the existing connection/access
  transaction and admission hold. Use native catalog names, explicit reviewed
  tool filters and the effective Pixel policy; adding a registry entry or seeing
  tokens must not grant every MCP server. Verify a real permitted read and a
  refused write through Pixel after activation.
- Removing an app must withdraw its tool grants and dispose the active gateway
  credentials under that same lifecycle. Local forgetting and provider-side
  revocation need honest separate outcomes. Preserve unrelated connections.
- Settings must distinguish saved, signed in, reachable and usable by Portal,
  and expose one clear next action/result in the existing ODS design. Do not
  forward raw native probe output: it includes launch parameters.
- Qualify update/rollback, interrupted consent/activation, native Mac and actual
  account consent/refresh/disconnect before claiming end-to-end readiness.

## Candidate external app

Notion is a candidate for a first remote read workflow, not an enabled catalog
item. Its [official connection documentation](https://developers.notion.com/guides/mcp/get-started-with-mcp)
specifies `https://mcp.notion.com/mcp` and OAuth. Its
[tool documentation](https://developers.notion.com/guides/mcp/mcp-supported-tools)
documents `notion-search` and `notion-fetch`. A narrow ODS read tool filter is
distinct from the provider's broader account authorization. The
[provider's security guidance](https://developers.notion.com/guides/mcp/mcp-security-best-practices)
requires treating returned content as untrusted and reviewing data sharing.
Notion can return content from other sources connected to the account, so the
reviewed app disclosure includes that reach and selected-model-provider sharing.
No real account was connected or client registered against Notion during this
qualification. Runtime tool availability and workspace administration still need
checking against the owner's actual account.

Google's current wizard explicitly discloses cloud-model sharing and submits
`cloudModels: true`. The stored false flag does not currently select an enforced
local-only route. Do not offer a per-app local-only switch until both actual
provider routing and retained conversation/tool content enforce that promise.
