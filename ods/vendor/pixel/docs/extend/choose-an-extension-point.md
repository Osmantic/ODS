---
title: Choose a Pixel extension point
doc_type: concept
audience: [contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, architecture, security]
sources_of_truth: [CUSTOMIZATION.md, scripts/limb-kit.py, scripts/configure.mjs, OPERATIONS-LIMB.md, FRONTIER-LIMB.md, DEEP-WORK.md]
last_verified_at: 2026-08-27
---

# Choose a Pixel extension point

Choose by data flow and authority, not implementation convenience. If an extension needs more authority than its seam permits, move it to the next isolated broker boundary instead of weakening the seam.

| Need | Use | Authority boundary | Status note |
|---|---|---|---|
| Add sanitized read-only data from a local/offline worker | Signed projection limb | Generated gateway adapter reads bounded projection; worker has no network/credential/process authority | Development preview; not a stable third-party compatibility promise |
| Declare observe-only tools already provided by that limb | Local capability pack | Data-only declaration; `observe-only`, no raw content storage | Inert until signed limb is enabled and reconfigured |
| Reuse fixed remote target helpers | Operations action pack | Namespaced fixed helpers, private target mapping, base policy/grant/lease still controls execution | Declaration/binding adds no target or approval authority by itself |
| Narrow an existing typed external review | Frontier task pack | Restriction only; cannot add task, provider, credential, prompt, or data category | Base Frontier policy/approval/budget still controls egress |
| Load reviewed gateway plugin code | `gatewayExtensions` | Explicit ID/path/tree digest/tool allowlist; no managed namespace override | Code-bearing, high risk; does not grant tools merely by loading |
| Customize commercial/client contract around immutable core | Client overlay | Private data contract; no core modification or credential | Schema validity is not legal/operational acceptance |
| Add a new source credential or write path | New broker/actuator core change | Dedicated identity, projection, separate actuator, full release gates | Not a pack shortcut |
| Add long-horizon runner/capability behavior | Deep Work core design | Separate broker/lease/runner/verifier boundary | Runtime currently development-disabled |

## Representative choices

- Local disk-health summary with no credentials: projection limb plus observe-only local declaration.
- Fixed service restart on enrolled lab targets: Operations action pack mapped privately to existing targets; not a gateway extension.
- Smaller token/classification envelope for plan review: Frontier task pack; not a new prompt class.
- Discord or another reviewed OpenClaw plugin: explicit gateway extension with exact tree/tool declarations.
- New authenticated SaaS source: dedicated source broker and projection plus separate actuator; never put the token in a limb worker or gateway plugin.

Every extension needs negative tests, install/enable/disable/remove or rollback behavior, status labeling, and documentation. Start with [projection limbs](projection-limbs.md) or [gateway extensions](gateway-extensions.md).
