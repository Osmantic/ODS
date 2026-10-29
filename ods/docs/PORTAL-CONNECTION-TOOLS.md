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
- An unreadable configuration or an active transition follows the native
  tool-error path, with an explicit unverified result. A successful IPC reply
  alone is not successful evidence about an account. Nullable presence flags
  from a failed read are not presented as an empty-account observation.
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

## Failed evidence at delivery

Real local 9B trials selected the status tool correctly with available state,
but also produced false absence claims after unavailable or unknown results.
A native tool error alone did not prevent those claims. The failed runs remain
negative model evidence.

The existing run guard now tracks attempted Google lookups and their typed,
call-bound results. A validated known Root status can resolve a pending account
check; a strict same-source receipt can resolve a source lookup. A cached read
does not resolve an unknown Root account status. Catalog entries, unrelated MCP
tools, discovery and older parallel replies cannot substitute for these facts.
The tracker stores identities and obligations, not account records.

Independent work can continue. If an obligation remains unresolved at delivery,
the existing host verification path replaces unverified free prose with
`Could not verify the requested Google lookup.` It preserves independently
verified artifacts and previews. Retained JSON/SSE results use the corrected
reply, and the existing host delivery record carries it into the next turn.
This uses observed tool results, not phrases in the question or answer.

This is a failed-evidence guard, not a universal claim checker. It cannot infer
a missing connection capability without an attempted observation. Arbitrary
mixed prose cannot be retained sentence by sentence without a claim parser;
the bounded fallback replaces that prose while verified outputs remain. The
host fallback currently uses English, matching the existing verification path.
The model's raw reasoning remains separately qualified from the delivered reply.

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
