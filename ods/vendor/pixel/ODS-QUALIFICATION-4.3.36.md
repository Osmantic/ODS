# ODS-maintained Pixel 4.3.36 candidate

Functional source checkpoint: `1220b3630f5d35ba687ec59a18a7638e127a3f6f`,
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
  package with the current ODS profile repairs. This uses a controlled provider,
  not inference or a real remote OAuth account. Native execution is confined to
  the fixture's existing MCP-only tool policy; Docker sandbox execution is not
  qualified by it. No installed runtime or access mode was changed.
- The macOS config/migration contract suites passed 65 cases on Linux/WSL,
  covering private staging and rejection of added, changed and null registry
  states. These are preparation/preservation checks, not real Mac qualification.

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
