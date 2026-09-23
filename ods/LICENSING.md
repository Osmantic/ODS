# Licensing in ODS

ODS is a mixed-license repository. The [root Apache-2.0 license](LICENSE)
applies to ODS code **except** the Pixel source under `vendor/pixel/`.

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
