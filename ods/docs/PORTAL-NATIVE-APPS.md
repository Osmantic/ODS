# Portal and native OpenClaw app connections

ODS uses the MCP registry, OAuth implementation and private credential store in
its pinned OpenClaw package. It does not implement another MCP client, credential
vault or scheduler. The development branch now includes authenticated reviewed
app enrollment in Connected apps, starting with Notion search and read tools.
Real Notion account consent, native Mac and update/rollback acceptance remain
release gates; controlled fixtures do not replace those checks.

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
That direct SDK operation stays private. The reviewed-app facade below owns the
public selection and complete enrollment journey.

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
configuration validator. The reviewed-app controller and facade below compose
the public journey with a post-activation readiness check.

`check_access` joins that check to fresh Root runtime proof and rechecks both the
configuration binding and runtime revision afterward. `forget_disabled` also
requires the exact disabled configuration to have verified runtime proof; a
file edit without a verified restart is insufficient. The owner independently
checks the disabled entry before forgetting. Its result explicitly says
`providerRevoked: false`. These helpers remain private; the managed controller
and reviewed-app facade compose them without exposing raw SDK operations.

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
same Root lock. Model/settings/provider/source writers also refuse fresh
mutations across an enrollment intent. The host/API/Settings journey below is
implemented; installed update/rollback and real-provider acceptance remain open.

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

## Authenticated reviewed-app journey

Connected apps now includes **Apps for Portal**, independently of Google's
status frame and sign-in. The first release-reviewed item is Notion, with only
`notion-search` and `notion-fetch` in its Portal contract. Its provider consent
can be broader: the screen discloses access to other Notion-connected sources
and sharing read content with the conversation's selected model provider.
It does not offer Coffee's custom MCP configuration, Home Assistant, ntfy or
Obsidian setup.

`native_public.py` accepts only the reviewed app ID and composes the existing
controller under one bounded operation deadline. It neither selects registry
paths nor creates another OAuth client/store. `finish` recovers the same flow
and explicitly activates it if authorization is confirmed; it never restarts
OAuth or replays a code. The controller's existing locks, exact plans, claims,
owner transactions, admission gates and rollback remain the authority.

| Dashboard route | Body | Root action |
| --- | --- | --- |
| `GET /api/pixel/connections/apps` | None | Cached scoped status; no Node/provider work |
| `POST .../apps/notion/connect` | `{}` | Disabled preparation, then native sign-in |
| `POST .../apps/notion/complete` | `flowId`, full `address` | Bound callback; authorization is not readiness |
| `POST .../apps/notion/finish` | `flowId` | Recover/activate/check the same saved attempt |
| `POST .../apps/notion/check` | `{}` | Check an existing grant without starting consent or changing access |
| `POST .../apps/notion/cancel` | `flowId` | Cancel only an unclaimed flow; otherwise require settlement/withdrawal |
| `POST .../apps/notion/disconnect` | `{}` | Withdraw, prove disabled runtime, then forget locally |

The API, host, Edge and Root reject additional fields. Browser input cannot
select a URL, server, executable, owner, tool set or transaction ID. The host
reuses the existing one-shot listener class with separate app/flow ownership;
its callback URI comes from its own bound loopback port, never a Host header.
Root compares the complete URI and state to its saved intent. Codes cross
bounded private request frames, not argv, logs, browser storage or journals.
Native validation failures, including malformed JSON and Pydantic rejections,
return fixed messages instead of echoing a rejected callback.

The Windows path also crosses the existing authenticated Node HTTP relay and
Root Unix socket. Only the native full-callback action gets the larger bounded
16 KiB frame; ordinary connection actions keep their 2 KiB limit. Root's long
actions share one 300-second deadline, including recovery followed by activation;
the Node, Edge, host and API budgets include bounded transport overhead. The
separate access check uses a 70-second Root deadline and bounded transport
overhead up to 90 seconds at the API.

A reused native sign-in returns no authorization URL and does not claim a newly
selected account. Settings reports Connected only from a verified
`pixel-owner-default` readiness result. Saved tokens, an OAuth reply or a journal
phase cannot produce that result. Status polls never initiate sign-in, activation
or a provider check. Expired readiness offers an explicit access check. Failed
or uncertain operations keep Finish/withdrawal available instead of claiming
ready/disconnected. Local removal explicitly reports `providerRevoked: false`.

An existing reviewed grant with no enrollment journal offers **Check access**
and Disconnect without inventing a consent flow or account-selection claim.
Check invalidates the old derived result durably before contacting the native
SDK. A failed, timed-out or lost check cannot reuse an older positive result.
Only an exact current policy/runtime proof can repopulate the private,
Root-owned cache for at most five minutes. Its binding includes owner, source,
gateway process, configuration, model, provider and the protected SDK policy
bytes. The canonical protected adapter supplies the policy-module inventory.

Ordinary chat, direct tools and cron change admission epochs without changing
that persistent identity. Status remains usable while those gates are idle or
busy, with no provider work during a poll. Held, interrupted, closing, stopped,
blocked or malformed runtime states refuse cached readiness. This distinction
keeps normal turns from expiring a verified connection while refusing a
connection whose current runtime cannot safely use its grant.

Controlled journeys exercise the full Dashboard and host HTTP services, the real
owner-authenticated Node relay, real framed Unix transport and isolated Root/owner
transactions. They cover wrong state, a callback exceeding 2 KiB, a lost native
exchange reply, explicit reuse without account-selection claims, activation,
content-free status and verified withdrawal. Separate actual Edge HTTP/Unix tests
refuse extra configuration and false readiness. OAuth/provider responses, gateway
processes and privileged UID custody are simulated in those journeys; the pinned
native SDK lane separately qualifies native consent, refresh, store and policy.
No real Notion account or installed owner state is used.

## Remaining release acceptance

The following acceptance checks still apply to the public journey:

- With explicit account authorization, qualify actual Notion consent, workspace
  administration, refresh and withdrawal. Verify a permitted read and refusal of
  a write/unrelated server through Portal with the real provider's inventory.
- Qualify the installed Windows/WSL path, including Windows callback listener,
  actual Docker Edge/ingress, interrupted attempts and existing Google/plugin
  preservation. The controlled lanes qualify those contracts separately from
  production service custody and a real account.
- Exercise installed update/rollback with pending and ready app state. Source
  binding drift must remain recoverable and never overwrite newer Root choices.
- Qualify native Mac before adding an available Mac catalog. The current public
  host transport honestly returns unavailable on Darwin.
- Review the installed Settings journey visually, including remote-browser
  callbacks, uncertain outcomes and keyboard focus, before release acceptance.

## Candidate external app

Notion is the first fixed catalog item in this development branch; real account
acceptance is still pending. Its [official connection documentation](https://developers.notion.com/guides/mcp/get-started-with-mcp)
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
