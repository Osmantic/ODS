# Dashboard asset notices

- Inter and JetBrains Mono are bundled locally under the SIL Open Font License
  1.1. Copyright notices and the license are in `public/fonts/OFL.txt`.
- The ODS mark (`public/osmantic-isolated-os.png`), its favicon and the wallpaper
  collection in `src/assets/wallpapers/` were supplied by Gabriel. The existing
  contribution records permission asserted by that contributor; it does not
  establish each third-party rights holder's grant. The
  [per-file artwork evidence ledger](../../../docs/ASSET-PROVENANCE.md) records
  the remaining source/rights gaps. No open-source or public-domain status is
  asserted for these images. Resolve each gap before relying on redistribution
  rights; changing this notice does not clear the artwork.
- Pixel's mascot renderer and workbench adaptation come from the Osmantic Pixel
  project. Read the current [Pixel License for ODS](../../../vendor/pixel/LICENSE.md)
  and [third-party notices](../../../vendor/pixel/THIRD_PARTY_NOTICES.md).
  Retain those notices with Pixel-derived material. The older notices in
  `../../../docs/pixel/upstream/` are a historical snapshot, not the current
  distribution grant. This contribution does not relicense upstream material.
- Third-party JavaScript packages retain their own licenses. Dependency versions
  and integrity hashes are recorded in `package-lock.json`.

No external image or font service is contacted to render the themes or profile.
Profile photos are cropped/resized in the browser and saved in that browser's
local storage; they are not uploaded to the model or a remote image service.
