# ODS-maintained Pixel 4.3.36 candidate

Functional source checkpoint: `2ac52729f`,
prepared locally on `feat/portal-native-integrations-20261026`. This is an ODS
candidate, not an upstream Pixel release or completed platform qualification.

Pixel's renderer previously rebuilt the configuration without `mcp`, removing
the native OpenClaw connection registry and the sandbox grants that let Portal
use it. The renderer now retains that registry, transport parameters, enabled
state and filters. OpenClaw continues to own connection execution and OAuth;
there is no second registry, credential store or MCP implementation.

Existing MCP grants survive along with restrictive global/agent/provider/sender
policies. Fresh managed-source denials still win. A registry entry alone adds no
tool grant. The helper does not start a server, inspect credentials, authenticate
an account or grant an unconfigured app. Native validation checks the candidate.

The macOS preparation path renders against a private, disposable copy of the
selected previous configuration. It leaves the active configuration intact.
Migration verification rejects adding or changing the native registry even when
the candidate digest is recomputed. Activation remains a protected operation.

The version advances from 4.3.35 because held source upgrades require an
increasing version. OpenClaw, runtime/dependency versions, image digests and
trust anchors remain at their existing pins. No release signature is generated.

The unpublished candidate also checks the declared required parameter names
before a Source Broker handler reads a projection or creates a proposal. A
deferred call using `id` instead of `messageId` receives the missing field and
the schema-discovery route, rather than a misleading record-not-found error.
A draft call without `body` cannot create a partial proposal. This checks missing
fields, not the complete JSON Schema; existing provider/approval validation and
tool policies remain authoritative. It adds no aliases or automatic retries.

Calendar reads additionally derive start/end labels from source timestamps,
including the weekday, source timezone or explicit UTC offset, and exclusive
all-day end dates. This addresses a local 2B model that read the correct date
and room but invented the weekday. Source timestamps, coverage, freshness,
read permissions and persisted projections are unchanged. Unknown/floating
times, invalid dates and invalid timezones receive no derived label; source
labels cannot override the computed values. Calendar interval schemas also
explain that their bounds need full timestamps rather than clock-only times.

The existing Source Broker plugin suite covers UTC, dates shifted by an event's
timezone, both sides of the repeated daylight-saving hour, offset-only timestamps,
all-day exclusive ends, invalid inputs and unchanged projection bytes through
both list/get tools. The local compact 8K prompt/read-surface experiments remain
outside this bundled source; they are not production or general-model acceptance.

Evidence from the functional checkpoint:

- `ods/tests/test_pixel_connection_planner.py`: 19 cases passed on Linux/WSL,
  including real configure/render/ODS overlay and planner equivalence through
  email/wiki connect/disconnect, retained read grants and delete denials, and
  no automatic grant for a configured or disabled server.
- `ods/extensions/services/pixel-agent/tests/test_native_mcp_runtime.py`: the
  pinned OpenClaw gateway and ODS Pixel plugin, after real re-render and overlay,
  refused an explicitly requested excluded write, delivered an allowed native
  read to a controlled loopback provider, and returned its unique synthetic fact.
  The corrected qualification at `593b5932e` requires the Pixel plugin to be
  imported and loaded from the exact staged root before the turn. It normalizes
  fixture file permissions, uses a coherent native execution mode, refreshes the
  registry and supplies Portal's canonical conversation namespace. An earlier
  fixture could pass its native MCP assertions without Pixel registering; that
  earlier timing is not qualification of the complete Pixel path. The corrected
  test passed in 41.013 seconds against a private copy of the exact ODS-installed
  package with the listed current image/compaction repairs. This uses a controlled provider,
  not inference or a real remote OAuth account. Native execution is confined to
  the fixture's existing MCP-only tool policy; Docker sandbox execution is not
  qualified by it. No installed runtime or access mode was changed.
- The macOS config/migration contract suites passed 65 cases on Linux/WSL,
  covering private staging and rejection of added, changed and null registry
  states. These are preparation/preservation checks, not real Mac qualification.
- `tests/source-broker-plugin.test.mjs`: the complete suite passes twice with
  the exact locked TypeBox 1.3.13 dependency. Its three new regression cases fail
  against the preceding handler and pass against this one: missing arguments
  fail before projection access, the correct `messageId` subsequently reads the
  actual record, and missing draft/calendar fields leave no partial proposal.
  The offline schema-builder fixture also passes. Broker and release-dependency
  suites pass 85 cases. These checks do not establish small-model reliability;
  a real 2B/8K natural task still repeats catalog searches and remains unqualified.

Before accepting this candidate, complete bundle/source verification, the
vendored release and end-to-end suites, held Linux/WSL source-upgrade checks,
and native Mac update/rollback qualification. Real remote OAuth consent,
refresh/revocation and the full Portal owner journey remain separate acceptance
work. Do not infer those from registry retention or the controlled-provider test.

The direct release-contract check currently stops at the existing Codex 0.147
comparison fixture: its committed SHA-256 is
`294602e3502bb0ff840a82cd1d08ac526125a6cdf763e1f883e8d74f47cc2a8d`,
while the contract requires
`ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807`.
The file is unchanged from the previous candidate and has no CRLF conversion.
Restore or otherwise resolve the exact reviewed comparison input before claiming
this gate passes; do not replace its expected digest just to accept other bytes.
