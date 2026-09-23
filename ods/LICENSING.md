# Licensing in ODS

ODS is a mixed-license repository. The [root Apache-2.0 license](../LICENSE)
applies to ODS-authored code **except** the Pixel source under `vendor/pixel/`.
Third-party files, dependencies, models, and assets retain their own licenses;
the repository license does not replace those terms.

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

## Contributions

Contributions to ODS-authored Apache-2.0 files are submitted under Apache-2.0.
No separate CLA is required by this guide for those files. Keep third-party
notices and confirm permission before contributing third-party material.

For `vendor/pixel/`, the outbound terms remain the existing Pixel License for
ODS. The Apache contribution statement does not grant rights in this directory.
The current Pixel license does not define an inbound contribution mechanism.
Maintainers and the contributor must document an appropriate inbound grant
before accepting a Pixel change. Until that is resolved, a Pixel pull request
must not be treated as Apache-licensed or as an implicit transfer of rights.
Maintainer and legal review of that mechanism remains a release requirement.

## Historical Pixel notices

The notices in [the 4.3.24 attribution snapshot](docs/pixel/upstream/README.md)
are retained historical upstream records. They are not the operative license
for the bundled Pixel source. Use [vendor/pixel/LICENSE.md](vendor/pixel/LICENSE.md)
and [vendor/pixel/THIRD_PARTY_NOTICES.md](vendor/pixel/THIRD_PARTY_NOTICES.md)
when reviewing or redistributing this ODS distribution. No license text has
been replaced by this overview.
