# ODS Source 4.3.46 Calendar composition candidate

Date: 2026-11-03. Functional checkpoint: `b8cb7c444699092dd027eadaa0b34f30a5e6b442`.

This combines the two different 4.3.45 packages without treating their shared
version number as proof of identical behavior. The ODS typed-window candidate
uses source ref `3fe50f1d75d13295bfbe964bfcd327a91df15814`; the upstream all-day
repair uses `47706f6863f74ae65c83c925bd6af51395193363`. Their original evidence
bytes and separate compatibility rows remain preserved. Neither is rewritten.

List and count share one strict, timezone-aware selection engine. Native list
calls require typed windows; malformed/mixed arguments refuse before projection
access. Explicit civil dates retain the pinned compiler's ASCII digit schema.
All-day bounds use the provider calendar timezone, including DST and non-hour
offsets and the exclusive end. Possible matches with unresolved all-day bounds
remain readable with a derived `timeWindowUnverified` flag, but never enter the
confirmed count or certify an empty/busy slot. Missing owner timezone keeps the
window unavailable and list total unknown. Source-supplied flags cannot forge
certainty. Returned coverage reflects unresolved windows, relevant uncertain
events, freshness and sync; stored projections remain unchanged.

Actual Node 24 component tests with the exact locked TypeBox and pinned pristine
OpenClaw package passed 51 cases with no skips: Source handlers, shared count
queries and registered Calendar list/count consistency. This includes the
upstream Los Angeles 25-hour day and Kathmandu boundary cases, advisory matches,
pagination versus confirmed counts, missing/invalid zones and malformed bounds,
exclusive boundaries, zero source reads for invalid arguments, spoofed flags,
and preservation of raw projections. The nine registered read tools total
7,608 bytes, within the unchanged reviewed 8,192-byte family cap. These are
component/contract tests, not an installed 4.3.46 model or macOS execution claim.

The earlier 4.3.45 ODS candidate separately passed the normal Windows installer,
independent installed-state checks and one actual Portal Qwen3.5 9B Calendar
journey after cleanup of duplicated WSL mounts. That evidence belongs to its
exact earlier identity; it does not qualify this combined 4.3.46 source, prevent
mount amplification, establish an error rate or qualify physical macOS.

No private historical audit, private ancestry, signing material or workflow is
added to the public source. The upstream full-repository contract still requires
its intentionally omitted historical audit inputs; any check with those inputs
must be reported separately from the public export. This remains an ODS-only
candidate with no signed upstream or supported-host promotion.
