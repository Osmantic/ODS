# Dashboard asset notices

- Inter and JetBrains Mono are bundled locally under the SIL Open Font License
  1.1. Copyright notices and the license are in `public/fonts/OFL.txt`.
- The 12 current JPEG wallpapers in `src/assets/wallpapers/` are AI-generated
  replacements created for ODS on 2026-09-24 under maintainer authorization.
  They use generic text prompts, with no former wallpaper supplied as an input,
  and are distributed under the repository's Apache-2.0 license to the extent
  applicable rights exist. They are not device-vendor wallpapers or location
  photographs. The [provenance ledger](../../../docs/ASSET-PROVENANCE.md)
  records concepts, dimensions and master/distribution hashes. Older images
  remain in history and older releases; this notice grants no rights in them.
- The ODS mark (`public/osmantic-isolated-os.png`) and favicon were supplied by
  Gabriel with asserted permission. Preserve a durable source/authorization
  record before representing downstream rebranding or redistribution as cleared.
- The ODS wordmark the dashboard shows (`src/assets/ods-mark.png`) is derived from
  that mark: its O is the supplied pixels, with the outer ring's opening on the
  right closed by the ring's own mirrored left side; its S and speed lines are
  the supplied pixels scaled by about 8% and raised level with the O; the D
  between them was drawn for ODS on 2026-10-15 in the O's ring geometry and
  gradient. It carries the supplied mark's status above.
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
