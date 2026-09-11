# Portal public beta — 9 September 2026

Portal (currently labelled Pixel in parts of ODS) is a core ODS feature under active qualification. This branch is the common baseline for supervised fleet and external user tests. It is not a declaration of general release readiness.

## Included changes

The integration at `3861c14cfc4b1a4e0dd5a261f2344a36bb49cfa8` combines:

- PR #3385 through `7dbddb225de63793fc29178165372c4eabae32ca`: background-process completion accounting, recovery into permitted work after a denied network request, and SVG file delivery without an unrequested website-publication requirement. Explicitly requested previews still require publication evidence.
- PR #3818 through `91050302d87bebf94696335e37a38d3388206ac7`: provider routing, runtime settings, and Apply/Deactivate/Recover controls.
- The existing public-beta workspace and dashboard UI from `7dde42bf020fa50709beee785ef06f06f0d6b458`, including PR #4027.

These PRs remain open and unmerged. Publishing their composition here is beta testing, not user acceptance.

## Qualification evidence

The earlier composition passed 560 focused plugin checks and 638 frontend tests across 69 files, followed by a production frontend build on physical Tower3 with Node 22.23.2. The SVG successor passed 586 focused integration checks on the Tower3 guest. Its PR #3385 source also passed 736 plugin checks with one skip on physical Tower3 and all 32 applicable hosted checks. The frontend is unchanged by the SVG successor. An earlier guest run omitted a sibling module from its source snapshot and encountered extensive timing failures. Those results are retained as test-environment evidence, not counted as established product regressions.

The 8GB laptop was updated to PR #3385 source `7dbddb22`, preserving owner configuration, SDK and container identities. Its native tiny-SVG task now delivers the saved path correctly; root inspection confirmed the actual 164-byte SVG is valid. The agent's SVG read still omitted the image, so full readback/vision qualification remains open. Tower3 generated and published a maze whose movement and restart controls responded in the browser; completion of the win path is not yet qualified.

Tower2's protected runtime and dashboard/API/edge canary at beta `d2542b75` activated successfully after an exercised file/image rollback. The deployment helper needed a bounded transition journal and container recreation after the ingress socket directory changed. Native provider controls and inference are being qualified separately. This does not establish whole-installation or factory-core parity, and the other machines are not yet on a common installed beta revision.

Installed fleet state is tracked separately. A source commit, successful build, or isolated SDK fixture does not prove that a machine has been upgraded or that a native user journey succeeds.

## Known limitations to exercise

- The original 8GB laptop has reproduced a long model response ending at 8,192 output tokens with no visible content or created files. Installed request/response settings are under investigation; increasing context is not a verified fix.
- Public PDF ingestion can fail at extraction or reach an approval workflow that chat does not explain correctly.
- Diagnosing a registered local extension can choose a blocked network route and fail to finish its report. Discovery alone is not proof that the extension responds correctly.
- Research and generated applications still require checking the cited facts and actual rendered output. Some completion claims exceed the evidence.
- A Strixy research correction repeated six malformed edit calls, then claimed saved corrections despite an unchanged readback. A further user-directed retry successfully changed the file. Automatic tool-error recovery and honest completion remain qualification gaps; successful research retrieval alone does not establish factual accuracy or persistence.
- The upstream isolated browser runtime has passed a disposable navigation/authentication/private-address check. Factory browser provisioning, full access transitions, and clean-install/upgrade/rollback journeys still require native qualification.

When reporting a test, include the installed beta commit, machine/OS/GPU, model and context settings, the task, the observed result, and any artifact or screenshot. Keep credentials and private documents out of public reports. Preserve hardware-specific settings when comparing machines; shared source does not require identical model configurations.
