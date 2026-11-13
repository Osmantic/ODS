# ODS bundled Source 4.3.44 candidate

Functional source: `dc07fa669` (civil-date counts and bounded cold discovery schemas).

This is an ODS candidate source snapshot. It is not a signed upstream release,
production promotion, real macOS qualification, or installed acceptance result.
The preceding 4.3.43 and older evidence records retain their exact bytes.

The Calendar count API adds an exclusive `civil-days` input variant with an ISO
`date` and `days`. The existing configured owner timezone resolves both civil
midnights; the model supplies no UTC offset. Invalid dates, mixed `date`/`start`
arguments and non-unique midnight boundaries are refused. Count results still
return no event records and retain their existing coverage/freshness gates.

The independent ODS native compatibility repair adds one complete top-result
parameter schema to Tool Search, bounded to 4096 UTF-8 bytes. Other or oversized
results point to `tool_describe`. It changes no ranking, capability, execution,
policy filtering, initial tool visibility, sampler or model selection.

Local qualification used disposable fixtures and a private copy of the pinned
native SDK: Source query/plugin cases passed; native discovery and existing
8K/32K/64K surfaces passed; explicit NONE and prompt budget regressions passed.
The exact previous SDK recipe and receipt migrated to the new bytes, matching
a fresh repair and an idempotent rerun; restoration recovered pristine bytes.
The installed runtime, Google data and provider credentials were not changed.

The earlier real November 4 query used an incorrect UTC interval. That evidence
remains a failure for the owner's civil day. A normal Source 4.3.43-to-4.3.44
upgrade and a new real-owner regression are still required before claiming
that this candidate fixes the installed Calendar workflow.
