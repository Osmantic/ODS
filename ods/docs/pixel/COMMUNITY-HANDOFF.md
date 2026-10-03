# Portal public-beta community handoff

Updated 10 September 2026. The owner ended the continuous supervised fleet campaign to move to other priorities. This is a handoff of an experimental public beta, not completed release acceptance. The test heartbeat is paused, both task goals are cleared, and the five native ODS test sessions and owned local review/build jobs are terminal. ODS and model services remain available; the machines have not been shut down.

## Published code

- [ODS PR #3385](https://github.com/Osmantic/ODS/pull/3385): `5cb98461f9bd2f9612d56dd6a632d7d2b1a13539`, open and unmerged.
- [ODS PR #3818](https://github.com/Osmantic/ODS/pull/3818): `c379453a133a4d7a0b8ca6dd9d7f9e4e552baa16`, open and unmerged.
- Runtime composition: `bef2245ad05ca4d4ea457b841f7773a994258d9b`, published through public-beta `83949ded364d3968738418d060b223d5ed6647ff`. This handoff is a documentation-only successor of that beta; it adds no runtime changes.
- The composition retains the OpenClaw base and includes the beta workspace/UI from [#4027](https://github.com/Osmantic/ODS/pull/4027). It is not the separate Hermes runtime transition. Portal/Pixel remains a core ODS feature, with other applications available in parallel.

Completed changes include native web-result projection; separate search and page-reading allowances; background-process completion accounting; file/SVG delivery without an unrequested website-publication requirement; provider and model metadata distinctions; and the sister PR's provider routing, runtime settings and guided connection import. Output limits now inherit through the existing defaults, selected-model and agent parameter layers. The last change did not simply raise context or output limits.

Search is intended to operate independently of Perplexica or a SearXNG application. The native keyless implementation is also tracked in [Pixel PR #240](https://github.com/Osmantic/Pixel/pull/240). Existing owner-selected search settings are preserved by upgrades; fresh installations and historical machines can therefore select different providers.

## What the evidence proves

The exact latest composition passed 713 frontend tests, 64 connection/host tests, 110 provider/API tests, 277 settings tests with three platform skips, 40 settings API/host checks and a production frontend build. Repository-configured Python lint passed; frontend lint reported zero errors and 501 warnings. These are source/disposable checks.

Twenty-seven output declarations were compared with the actual pinned SDK resolver. Ten SDK-generated HTTP requests, including override, reset and rollback cases, passed an owned fixture gateway. This verifies serialization/validation in those cases; it does not qualify installed cloud inference, two-host sharing, sampling, thinking, or Full Access transitions.

Recent real ODS journeys produced these results:

| Surface | Observed result | Remaining limit |
| --- | --- | --- |
| Tower1, large CSV | A native task summarized 20,000 rows. Independent streaming Decimal analysis reproduced total `799900.00`, all three category totals, 2,000 negative and two zero amounts; the input hash was unchanged. | The subsequent reusable analyzer and its malformed/quoted-field fixtures completed, but its final claims were not independently requalified before shutdown. |
| Strixy, standalone research | Activating the pinned native `parallel-free` plugin produced relevant restaurant/menu results and a saved/read-back comparison without Perplexica. | Direct menu pages often returned 403/challenge responses. The report confused excerpts with fetched pages, omitted then added questionable links, and retained arithmetic/counting and provider-attribution errors. Relevant results do not establish source-faithful research. |
| Strixy, documentation research | A later native task completed a source-linked ZIP-ingestion guide based on Python documentation. | Completion was recorded; the final guide was not independently fact-checked before shutdown. |
| Tower3, generated aquarium | Publication succeeded; a reviewer used the real preview's Pause/Play, speed and Day/Night controls. | Portal's own browser subsequently failed navigation to the published localhost URL with `browser navigation blocked by policy`. Source inspection was not an interaction test. |
| Laptop, 9B study app | The task survived browser reconnection and its completed preview was recovered. Real interaction exposed a negative countdown, inconsistent phase labels and incorrect duration initialization. | Two native repair turns published successors. The final snapshot, `site-ab1cc7c4bb05008044680e42`, was not independently browser-tested before shutdown. Do not count publication/readback as a timer pass. |
| Tower2 ODS guest | A readiness report completed and explicitly separated configured GLM metadata from unknown serving-model identity. | Actual model identity and the mixed installation remain unresolved. The ODS guest and physical inference host are different surfaces. |
| Browser images | Tower1's native browser captured PNG screenshots and followed example.com to IANA. | The screenshots did not reach the chat UI; image capture is not end-to-end image delivery. |

Earlier results and failures remain in [PUBLIC-BETA-QUALIFICATION.md](PUBLIC-BETA-QUALIFICATION.md). Historical statements about running repairs describe their checkpoint time, not an active campaign.

## Installed state at shutdown

| Machine | Installed state | New beta staging |
| --- | --- | --- |
| Tower1 | Full `7dcb7d8f` baseline plus targeted canaries | New dashboard/API images loaded only |
| Tower2 ODS guest | Mixed source/runtime; not declared aligned | New dashboard/API images loaded only |
| Tower3 | Verified `59acd787` source, with existing browser/search configuration | Exact 839 source and images staged; not activated |
| Strixy | Verified `59acd787` source plus the authorized native-search configuration | Exact 839 source and images staged; not activated |
| 8GB laptop | Verified `59acd787` source | Exact 839 source and images staged; not activated |

The new source stages contain 1,722 non-generated tracked files, with 16 changed paths from 59. The three 59 installations matched their expected before-state at staging. Owner/generated configuration was preserved. No 839 installation was performed before cancellation.

Both new images built successfully and were loaded on all five hosts without recreating running containers:

- Dashboard revision 839: `sha256:6ef3b2c8f0d07ef57dd3bac0bf59621973cdad784f78823761c308fa8e3bbc19`.
- Dashboard API revision 839: `sha256:2780392d6ca77c1b48677197382395220328a1ff24ad8bc5bbf53ea1db842686`.
- Source archive SHA-256: `9df94af9f899987a4838f692587780d60361f209fa5fc046bcb1d504fcae9567`.
- Image archive SHA-256: `eea31520bd9bc11b3b2580ec4c9366f61bd4036ac269aec65221ee8f017450cb`.

These are retained fleet qualification artifacts, not a promise that those local image tags are available from a public registry. Installers and testers should use the repository's documented build/install flow and record the version actually running.

## Highest-value remaining work

1. **Installation and model switching:** qualify one exact beta from fresh install through model change, restart, update and rollback on each supported surface. Preserve hardware-appropriate memory/context settings and distinguish loaded-model telemetry from configured aliases. A running service or source checkout alone does not establish a working chat.
2. **Research completion and fidelity:** retain exact source URLs through compaction, finish requested files, distinguish search excerpts from fetched pages, handle challenged pages honestly, and check that final claims match evidence. Tower1's research compacted at a recorded 33,753 tokens, then ended with 8,192 output tokens and no final answer blocks despite a 64K UI context. Capture the actual model request/response before changing limits or adding tool sequences.
3. **Browser and preview access:** make Portal's isolated browser able to test its own published artifacts. The latest trace identifies the upstream browser navigation-policy denial for a `.localhost` preview; browser status was enabled/running. ODS's public-only `web_fetch` separately denied the local URL. A proposed wildcard/private-network exception was not accepted or applied. A solution must retain useful public browsing and scope access to the intended preview without opening unrelated private services.
4. **Completion and continuity:** preserve the model's useful final response when a stale-preview warning is added; align Stop, recovery, progress and actual execution; deliver tool images through to chat. The warning currently can replace the completion. Test browser reconnection and interruption without duplicate execution.
5. **Ingestion:** provide safe document/photo/file upload in the interface, bounded archive and large-file processing, useful PDF/codebase/GitHub analysis and actual image interpretation. The Mention source control currently exposes conversation/evidence references, not a file uploader. Successful workspace-file analysis does not qualify upload or vision.
6. **Provider/access journeys:** installed output override/reset, two-host inference sharing with client-local tools, cloud routes, revocation/failover, and verified Full Access transitions remain unqualified. Do not infer these from fixture gateway or settings tests.

An operational update bug was also isolated: separate ingress stop/start recreated the runtime directory while Docker retained the old bind. The repaired fleet helper uses one ingress restart, verifies unchanged directory identity and checks public Pixel availability after releasing admission. Tower3 actually rolled back to 7d and reapplied 59 with those checks passing. This is operational evidence, not blanket qualification of the factory updater or every platform's lifecycle.

## Related PRs worth reviewing

- [#4103](https://github.com/Osmantic/ODS/pull/4103): avoid substituting configured model metadata when runtime telemetry reports no loaded model.
- [#4084](https://github.com/Osmantic/ODS/pull/4084): prevent stale setup polls from overwriting Stop results.
- [#4120](https://github.com/Osmantic/ODS/pull/4120): bound stalled restored-chat activity/result lookups without replaying the task.
- [#4110](https://github.com/Osmantic/ODS/pull/4110) and overlapping [#4111](https://github.com/Osmantic/ODS/pull/4111)/[#4121](https://github.com/Osmantic/ODS/pull/4121): storage-access and malformed-conversation recovery.
- [#4124](https://github.com/Osmantic/ODS/pull/4124): Python 3.10 timeout compatibility. Verify the standalone host dependency path, timeout/cancellation behavior and a real 3.10 run before accepting it.
- [#4125](https://github.com/Osmantic/ODS/pull/4125): Windows test collection hygiene; skips are not Windows runtime qualification.
- [#4119](https://github.com/Osmantic/ODS/pull/4119): conversation export for troubleshooting and user control.

These are review candidates, not merge approvals or claims that their changes are installed or included in this beta. The final priority is an ordinary user completing useful work; accumulating passing tests or more PRs is not a substitute.

## Reporting a community test

Include the exact installed revision, OS/GPU/RAM, serving model and context/output settings, reproducible user steps, expected and actual behavior, and relevant redacted logs or artifacts. Distinguish source tests, image builds, installed runtime checks and real user interaction. Record both recovered failures and successful outcomes. Keep credentials and private documents out of reports.

Continuous all-machine occupancy and the requested 90% direct-user-testing-time target were not achieved. The campaign is stopped by owner choice, with known incomplete work preserved rather than labelled complete. No additional automatic deployment or testing is scheduled by this campaign.
