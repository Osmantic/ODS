# Source update limits

`ods update` performs image/runtime maintenance. It does not fetch application
source or upgrade the native Pixel release. `ods-update.sh update`, including
the Dashboard update action, is a separate source-checkout operation.

The source updater refuses managed native Pixel and source-built Compose stacks
before snapshots or Git mutation because it cannot restore their previous
runtime artifacts. Invalid native state, unresolved Compose configuration, and
Compose versions without JSON configuration output also fail closed. Linux
source updates require the install-directory owner or root. Root inspects the
account database and owner receipts rather than trusting `HOME` or `SUDO_USER`;
ordinary root-owned appliances retain backup, restore and configuration rollback.
Native or unverifiable state still refuses these operations. The image-only CLI remains
available. These guards do not make the remaining source updater transactional:
its configuration rollback does not restore Git source or prior image IDs.

On macOS, the existing `pixel-native-update.py --install-dir EXISTING_INSTALL
--ods-source REVIEWED_ODS_SOURCE` coordinator updates the native Pixel deployment
from a separately reviewed checkout. Retain its preparation/recovery journals;
it does not qualify a whole-appliance upgrade. Do not replace an existing
installation with `--force` as an update workaround. Linux native transitions
require a reviewed installer-managed plan and separately verified owner-data
preservation; ordinary ODS backups must not be assumed to cover native Pixel.

These checks protect this updater version and later versions. An already
installed older updater continues executing its old code while pulling new
source; merging this change cannot retrofit its preflight. Main-to-candidate
installed migration remains unqualified. Release notifications compare version
tags, not branch commits or image digests. The `3.0.0` development version on
main does not announce or publish a new stable release; `v3.0.0` requires its
own qualified commit and release publication.

`ods rollback` delegates to the same configuration rollback path. Manual and
automatic rollback now check both the current native identity and the selected
snapshot's prospective native state before stopping Compose or restoring files.
Managed, selected, incomplete, or ambiguous native state requires deployment-
specific recovery; configuration-only rollback cannot substitute for it. Ordinary
non-native rollback remains available, with the same source/image transaction
limitations described above.
