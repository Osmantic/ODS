# Connection state in the Portal harness

Development work in the consolidation draft; this page does not establish
release acceptance or equal model quality across hardware and context sizes.

The owner question “meu email está conectado?” exposed a concrete failure:
Portal searched the installable extension catalog for Gmail and treated an
empty result as evidence about the account. The connection was healthy.
Neither a catalog lookup nor a GitHub installation-request status can establish
Google connection state.

## Model choice and runtime boundaries

- `pixel_ods_connections_status` is a read-only native tool with no arguments.
  It reports the saved Google connection, owner read permissions, the provider
  permissions observed during sync and the last source health observation.
  It returns no account address, credentials, OAuth URLs, approvals or records.
- The Root access service constructs and validates a closed projection. The
  gateway calls one fixed local helper through an authenticated Root socket;
  it does not use the owner HTTP key or expose the broader Settings response.
- Saved sign-in, allowed reads and recent source observations remain separate.
  `providerCheckedNow: false` means this was not a fresh authorization check
  against Google. Stale, unreadable, transitioning or unavailable state cannot
  establish that the account is disconnected.
- The model receives stable capability descriptions and chooses the tool.
  The connected-record phrase classifier and conditional per-turn read plans
  have been removed. The runtime still enforces tool policy, argument schemas,
  owner constraints and result validation independently of model reasoning.
- Native Tool Search retains every permitted tool. The small status schema is
  directly visible for qualified contexts of at least 32K; smaller contexts
  discover and describe it normally. Context comes from the model already
  resolved by the native gateway, with explicit configuration allowed only to
  reduce that window. A provider-only model configuration does not need a
  duplicate `contextTokens` field. Policy-denied tools are never synthesized.
  Counts use app count tools; record requests use app read tools. The extension
  catalog's description leads with its different purpose.

This covers the reviewed Google connector on Linux/WSL. Unsupported systems
return unavailable/unknown. It does not add Coffee's custom MCP integrations or
establish the state of arbitrary external apps.

## Validation required

Schema and transport tests verify redaction, fixed execution, cancellation,
unknown results and separation of connection, permission and sync facts. Native
catalog tests verify effective policy, discovery and dispatch with the pinned
OpenClaw package. Runtime budget tests remain separate from model quality.

Real-model acceptance must also inspect the actual tool choices and final
answer: the original question, different phrasings, a follow-up after a
no-tools request, disconnected and unknown states, denied tools and requests
that need no tool. Synthetic state validates model behavior with controlled
conditions; it does not verify a real Google account. An installed end-to-end
check must be reported separately. A model failure remains a failure even when
the wrapper or infrastructure exits successfully.
