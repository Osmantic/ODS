# Legacy extension provenance evidence

Reviewed 2026-09-23 for PB-013. All 34 legacy recipes named in the audit now contain `upstream.json` and `NOTICE.md`. The missing-record gap for this batch is addressed. **PB-013 remains open**: recording an upstream does not establish eligibility to redistribute every image, dependency or model.

The records cover 111 local runtime/configuration files, 44 literal image/base-image occurrences and 42 primary license documents. There are 27 image recipes, four local builds, one configuration server (Continue/nginx) and two disabled Docker recipes (Dify and Jan). Disabled recipes were not enabled, and native Jan installation is not claimed.

## Method and source binding

Public GitHub metadata, resolved commits and original license/notice files were inspected. Records link immutable source documents with SHA-256 observations. Source binding is explicit: 22 matching version references, eight current-source-only observations, one reference within a floating image's minor line, one npm-declared Git revision and two OCI-declared revisions. Version matching and publisher labels are not build attestations.

OCI configuration was inspected without pulling layers for CrewAI Studio, Forge, Piper, RVC, SillyTavern, text-generation-webui and XTTS. Piper and SillyTavern supplied revision labels subsequently checked against public source. No image signature, attestation or reproducible build was verified.

The Bark 0.1.5 and Open Interpreter 0.4.3 wheels were downloaded as inert archives, verified against PyPI SHA-256 and inspected without installation or execution. Public Hugging Face metadata and small license/model-card documents were retrieved for AudioCraft, Bark, Piper voices, RVC assets and XTTS-v2; no weights were downloaded. These observed revisions do not change runtime selections.

Each JSON records configured image literals, source and license scopes, original license/notice URLs, document hashes, normalized local input hashes, separate components and specific gaps. The ODS notices summarize provenance; they do not replace upstream licenses or grant rights.

## Remaining gaps by group

| Recipes | Evidence and remaining work |
| --- | --- |
| aider, anythingllm, chromadb, flowise, frigate, gitea, immich, invokeai, label-studio, langflow, librechat, localai, milvus, miniflux, ollama, paperless-ngx, weaviate | Configured-version source/license references recorded. Image-to-source proof, transitive and companion notices, provider contracts and any downloaded model terms remain separate. Frigate detector assets, Immich ML models and InvokeAI checkpoints still need model-specific inventory. |
| baserow, dify, forge, ntfy | Baserow separates MIT core/client code, CC-BY-SA documentation and commercial premium/enterprise terms. Dify 0.6.16 adds conditions to Apache. AI-Dock's current source has custom restrictions. ntfy declares dual Apache/GPLv2 plus separate third-party assets. Actual shipped component obligations remain unresolved; Forge's older image is not bound to the reviewed current license, and Dify's disabled image is not established as upstream's documented deployment. |
| bark, open-interpreter | Exact wheel/license hashes recorded. Bark 0.1.5 is MIT. **Open Interpreter 0.4.3 contains GNU AGPL v3**, although current source is Apache-2.0. Neither matching version tag resolved. Package-to-source binding remains open; current Apache is not substituted for the installed artifact's AGPL. Bark auxiliary weights and unconstrained dependencies remain unverified. |
| crewai, rvc, text-generation-webui, xtts | Third-party images are explicit. Atinoda wrapper/application sources are separate. RVC history identifies prebaked weight URLs. XTTS separates MIT server, MPL runtime and CPML weights. Image source commits, CrewAI/RVC publisher associations, inherited weight grants and dependency notices remain open. |
| continue, jan, jupyter | Continue actually deploys nginx; the IDE extension is separately installed. Its primary record therefore identifies nginx. Jan's compose is disabled. Jupyter's `python-3.11` tag does not identify an exact stack revision. None is represented as a proven image/source build. |
| gaia, piper-audio, sillytavern | GAIA npm metadata provides `gitHead`; Piper/SillyTavern OCI labels provide revisions. These are publisher claims. GAIA/Piper dependency inventory, Piper engine/runtime versions, its mutable image tag and the default Lessac voice grant remain unresolved. |
| audiocraft | Code at 1.3.0 is MIT; selected MusicGen/AudioGen weights separately declare CC-BY-NC-4.0. Startup downloads lack model revision/digest pins; restrictions and acceptance are not implemented by this metadata backfill. |

Individual evidence and exceptions are linked in each recipe, including [Baserow](../extensions/library/services/baserow/upstream.json), [Dify](../extensions/library/services/dify/upstream.json), [Forge](../extensions/library/services/forge/upstream.json), [Open Interpreter](../extensions/library/services/open-interpreter/upstream.json), [Piper](../extensions/library/services/piper-audio/upstream.json), [RVC](../extensions/library/services/rvc/upstream.json) and [XTTS](../extensions/library/services/xtts/upstream.json).

Piper's repository-level MIT label does not settle `en_US-lessac-medium`: its specific card links a [Lessac dataset agreement limited to research](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html). A distinct grant for those trained voice weights remains unestablished. RVC's minimal root MIT card likewise does not establish the original terms/revisions of each Hubert, RMVPE, UVR and pretrained RVC file.

## XTTS consent correction

The previous recipe set `COQUI_TOS_AGREED=1`; the pinned image's default command also piped `y` into the server. Both automatic paths are removed: Compose requires a nonempty operator value, and the replacement command requires exactly `1` before running the server. Stdin is closed and the NVIDIA entrypoint is preserved. The command explicitly uses port 80 to match the existing environment, published target and health check.

The manifest requests this setting without a default; the extensions catalog was regenerated. Operators review the [official immutable XTTS-v2 CPML text](https://huggingface.co/coqui/XTTS-v2/blob/6c2b0d75eae4b7047358e3b6bd9325f857d43f77/LICENSE.txt) before setting the value. The original `coqui.ai/cpml` model-card link returned 404; the official model repository license was retrieved successfully. Its non-commercial scope is independent of MIT/MPL code terms.

This uses existing operator environment configuration, without a new persisted acceptance UI. Missing/empty values fail before container creation. Other invalid values stop the command before server/model loading; the existing restart policy can retry an invalid nonempty setting until corrected or stopped. Exact model revision and eligibility remain unresolved. Already-running deployments need the updated recipe and container recreation for the guard to apply.

## Validation and CI

The dedicated [workflow](../../.github/workflows/test-extension-provenance.yml) covers recipe inputs, gate, tests, this document and its own changes on push/PR to `main` and `public-beta`. Actions are pinned to immutable commits. Python checks use the standard library. XTTS tests use the runner's Docker Compose only to render JSON, then execute a temporary harmless `python3` stand-in. No package installation, image pull, container start or model download is required.

Verified locally:

- `python ods/scripts/audit-extension-provenance.py`: 34 records, zero errors.
- `python -m unittest discover -s ods/tests -p test_extension_provenance.py -v`: seven tests, including all 34 records and negative cases for missing evidence, wrong image identity, source overclaims and input drift.
- `python -m unittest discover -s ods/tests -p test_xtts_consent.py -v`: four tests, including six operator values, missing/empty Compose rejection and AMD/NVIDIA overlays.
- Existing `audit-extensions.py --include-library` for these 34 IDs: zero errors/warnings.
- Catalog regeneration changes only the XTTS environment declaration and generation timestamp.

The gate checks coverage and drift, **not license clearance**. Remote CI has not run for these uncommitted changes. Full XTTS speech/GPU execution and downstream compliance were not tested.
