# Third-party licensing review

Reviewed 2026-09-24 UTC against ODS
`1bc5e1cbb24864f1c9efd551e0613202af324e3f`.

This page separates documentation corrections from unresolved rights evidence
and runtime work. It does not grant new rights, certify legal compliance, or
claim that passing technical tests establishes permission to redistribute.
The governing starting point is [ODS licensing](../LICENSING.md).

## Services and use restrictions

| Component | Terms checked | Operator or distributor action |
| --- | --- | --- |
| Pixel | [Pixel License for ODS](../vendor/pixel/LICENSE.md) | Keep the ODS-only grant and third-party notices; do not extract Pixel into another product. Contributions need an agreed inbound basis. |
| OpenClaw used by Pixel | [Current generated notices](../vendor/pixel/THIRD_PARTY_NOTICES.md) | Retain upstream MIT and incorporated third-party notices. Do not apply Pixel's restrictions to OpenClaw itself. |
| Open WebUI v0.7.2 | [Pinned license](https://github.com/open-webui/open-webui/blob/v0.7.2/LICENSE) | Preserve upstream branding unless a stated exception or written permission applies; see [branding guidance](../extensions/services/open-webui/BRANDING.md). |
| n8n 2.6.4 | [Pinned Sustainable Use License and enterprise exclusions](https://github.com/n8n-io/n8n/blob/n8n%402.6.4/LICENSE.md) | Internal-business use and redistributing or offering n8n to others have different permissions. Review paid appliances and hosted offerings separately. |
| AudioCraft | [MIT code](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE) and [CC BY-NC 4.0 weights](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE_weights) | Do not infer commercial permission from the old catalog's royalty-free wording. Resolve the exact model terms and any separate output rights. |
| XTTS-v2 | [Coqui Public Model License](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt) | Review noncommercial model/output use and notice requirements. The current recipe supplies `COQUI_TOS_AGREED=1`; an explicit operator acceptance flow remains to be implemented. |
| Dashboard visuals and fonts | [Asset evidence ledger](ASSET-PROVENANCE.md) | Current wallpapers have been replaced with documented AI-generated artwork; historical versions remain uncleared. Preserve OFL notices. |
| Curated model downloads | [Model terms source inventory](MODEL-TERMS-INVENTORY.md) | Verify original-model terms and quantizer provenance for each artifact; metadata tags alone are insufficient. |

This is a prioritized review, not a complete license inventory of every package
inside every upstream image. Pulling a third-party image is distinct from
redistributing its bytes. If ODS or a downstream appliance distributes those
bytes, separately verify notice, source-offer and other obligations for the
actual built distribution. Container boundaries alone do not answer that question.

## Recipe provenance coverage

The source contains 171 library recipe directories: 137 include `upstream.json`
and 34 do not. The latter also lack a local license file. The strict extension
auditor passes 203 total service definitions; that structural result does not
close these provenance gaps.

Recipes missing structured upstream records at the reviewed commit:

`aider`, `anythingllm`, `audiocraft`, `bark`, `baserow`, `chromadb`, `continue`, `crewai`, `dify`, `flowise`, `forge`, `frigate`, `gaia`, `gitea`, `immich`, `invokeai`, `jan`, `jupyter`, `label-studio`, `langflow`, `librechat`, `localai`, `milvus`, `miniflux`, `ntfy`, `ollama`, `open-interpreter`, `paperless-ngx`, `piper-audio`, `rvc`, `sillytavern`, `text-generation-webui`, `weaviate`, `xtts`.

The [recipe source register](RECIPE-SOURCE-REGISTER.md) now provides pinned
upstream license evidence for all 34; image-to-source provenance remains open.

Backfill source repository, exact ref/image, application and model license
distinctions, required notices and any restrictions. Verify against upstream;
do not infer a license from an ODS wrapper, an image name, or a project's older
release. A local notice is not universally mandatory for a recipe that merely
references an external image, but an accurate source/terms record is needed to
review what the recipe installs.

## Remaining work and acceptance evidence

| Item | Remaining work | Completion evidence |
| --- | --- | --- |
| Artwork follow-up | Current 12 wallpapers replaced with recorded generation provenance; retain records for ODS marks and do not redistribute old wallpaper versions as cleared | Current asset hashes match the ledger; brand authorization records retained |
| XTTS consent | Replace hardcoded agreement with an explicit reviewed acceptance mechanism | Declining or missing acceptance prevents download/use; a recorded choice binds the presented terms |
| AudioCraft catalog copy | Correct manifest/generated-catalog wording together | Regenerated catalog has no unqualified royalty-free claim; catalog consistency checks pass |
| Model and recipe terms | Validate all original sources, terms, notices and acceptance needs; integrate a reviewed runtime schema separately | Complete artifact-bound records and tested presentation/acceptance behavior |
| Vendored Pixel documentation | Repair references to omitted private audit files when the Pixel source bundle can be regenerated | Visible source and bundle still match; link and generated-release checks pass |

The cleanup leaves runtime metadata, service recipes, installers, model
settings and the Pixel source bundle unchanged while runtime testing is
underway. The separately authorized artwork change replaces the 12 wallpapers
and updates three visible wallpaper names; saved theme IDs remain unchanged. These open items must not be
reported as resolved merely because their documentation is clearer.
