# Licensing in ODS

ODS is a mixed-license repository. The [root Apache-2.0 license](LICENSE)
applies to original ODS code **except** the Pixel source under `vendor/pixel/`
and material carrying separate third-party notices. It does not relicense
downloaded service images, model weights, bundled fonts or artwork.

Pixel is source-available under the separate
[Pixel License for ODS](vendor/pixel/LICENSE.md). It allows personal and
commercial use, modification, and redistribution of Pixel **within ODS**,
including modified or forked ODS distributions. It does not allow extracting
Pixel as a standalone product or using it in another product. Merely seeing
Pixel source in this public repository does not make it Apache-2.0 or an
open-source license under the Open Source Definition.

The local `vendor/pixel.bundle` is a single-commit installation artifact built
from the same visible `vendor/pixel/` source. It carries the same Pixel license;
`python3 scripts/verify-pixel-bundle.py` checks its digest, source match, and
one-commit history. It does not contain the private Pixel repository history.

Third-party components retain their own terms; see
[Pixel's notices](vendor/pixel/THIRD_PARTY_NOTICES.md) and notices elsewhere
in ODS. This overview does not replace those licenses.

## Component boundaries

| Material | Where to find its terms |
| --- | --- |
| Original ODS code and documentation | [Apache-2.0](LICENSE), subject to per-file notices |
| Bundled Pixel and Pixel-derived material used within ODS | [Pixel License for ODS](vendor/pixel/LICENSE.md); retain attribution and any third-party notices |
| OpenClaw runtime and derived patches | [Pixel third-party notices](vendor/pixel/THIRD_PARTY_NOTICES.md) and the [runtime source notice](extensions/services/pixel-agent/runtime-source/OPENCLAW-LICENSE) |
| Dashboard fonts and visual assets | [Dashboard asset notices](extensions/services/dashboard/ASSET-NOTICES.md) and [per-file artwork evidence](docs/ASSET-PROVENANCE.md) |
| Downloaded services and model artifacts | Their exact upstream versions and terms; see [third-party review](docs/THIRD-PARTY-LICENSING.md) |

The old notices under `docs/pixel/upstream/` are a historical attribution
snapshot, not the operative license for the current bundled Pixel. See that
directory's [scope note](docs/pixel/upstream/README.md).

Source availability and a successful installation do not establish permission
for every commercial, redistribution or branding scenario. Unresolved asset
rights and missing model/service records are listed in the review documents;
these pages do not grant new rights or certify legal compliance.
