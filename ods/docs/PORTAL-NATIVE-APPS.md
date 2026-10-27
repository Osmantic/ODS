# Portal and native OpenClaw app connections

ODS uses the MCP registry, OAuth implementation and private credential store in
its pinned OpenClaw package. It does not implement another MCP client, credential
vault or scheduler. The local implementation is still in preparation; there is
no new public native-app enrollment endpoint or catalog item yet.

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
by qualification, not installed as an independently callable public service.

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

For the 8192-token primary Portal profile, native Tool Search keeps the schemas
of `pixel_gmail_search`, `pixel_gmail_read`, `pixel_calendar_list` and
`pixel_calendar_get` visible when those tools are already permitted and have
unique names. This lets a small model read their required arguments before its
first call. It retains the original tool objects, permissions and Source Broker
handlers; it cannot connect an account or restore a denied tool. Other tools,
including source writes, remain discoverable through the native catalog. The
smaller-context and larger-context surfaces keep their existing behavior.

## Wiring required before offering Connect

- The existing root custodian must select a reviewed app and native server
  entry, bind the managed config hash, owner, runtime package and exact native
  state directory, then launch the adapter under that identity with a deadline.
  Browser and model requests must not select package paths, launch commands or
  arbitrary server configuration.
- Persist only the connection intent/pending transition under root custody:
  native entry identity, state hash, exact redirect, expiry and phase. OpenClaw
  retains PKCE/client/token material. Claim a callback durably before exchange;
  reject replay, expiry, another server/config and unknown prior completion.
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
No real account was connected or client registered against Notion during this
qualification. Runtime tool availability and workspace administration still need
checking against the owner's actual account.

Google's current wizard explicitly discloses cloud-model sharing and submits
`cloudModels: true`. The stored false flag does not currently select an enforced
local-only route. Do not offer a per-app local-only switch until both actual
provider routing and retained conversation/tool content enforce that promise.
