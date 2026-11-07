# ODS bundled Source 4.3.45 candidate

Functional API checkpoint: `d56daf9cdbc50a079f91ed945be3d3a93ca38788`.
The identical API changes are retained in the ODS integration history.

This is an ODS candidate snapshot, not a signed upstream release or production
promotion. Earlier evidence records retain their original bytes. This record
does not claim a normal installed upgrade, physical macOS qualification, or
general model reliability.

Calendar list and count share deterministic window, event-boundary, timezone,
freshness and coverage handling. Native listing now requires the same typed
window as counting. Existing instant-based native clients must use
`window.kind: absolute`; flat and mixed native arguments are refused before
projection access. The internal helper retains a strict legacy adapter.
Count returns no event records. List page size is separate from a qualified
complete matching count. Missing or ambiguous time and incomplete coverage
never establish a complete zero. The date schema uses an equivalent ASCII
digit pattern accepted by the pinned grammar compiler.

Source query/plugin tests passed, including real locked TypeBox validation,
zero projection reads for invalid arguments, DST days, all-day events,
exclusive boundaries, partial coverage and pagination. The complete converted
32-tool surface compiled with the pinned b9014 bridge and Qwen3.5 template.

One isolated actual Qwen3.5 9B candidate run used the production prompt hook,
unchanged model parameters and 8192 output budget. It chose Calendar count once
with the owner's explicit civil date. The fresh complete result was three,
with Sao Paulo bounds 2026-11-04T03:00Z to 2026-11-05T03:00Z, excluding the
date-boundary decoys and returning no event records. It made no unrelated or
mutating calls. The final sentence gave the correct count rather than the
strictly requested bare number.

The earlier Source44 eager-schema candidate returned an incorrect UTC-day
count and remains rejected. A subsequent baseline accidentally started before
its grammar gate completed; it is retained as nonpaired evidence. The final
fixed candidate is a targeted functional success, not a matched-pair
error-rate claim. No owner Google data or installed files were changed by
these isolated model runs.

Normal installed acceptance and the separate SDK context-budget and rollout
gates remain required. Native macOS Source execution and official Google
onboarding have separate implementation and maintainer prerequisites.
