# Dashboard asset notices

- Inter and JetBrains Mono are bundled locally under the SIL Open Font License
  1.1. Copyright notices and the license are in `public/fonts/OFL.txt`.
- The ODS mark (`public/osmantic-isolated-os.png`), its favicon and the wallpaper
  collection in `src/assets/wallpapers/` were supplied for this contribution by
  Gabriel. The contributor confirmed permission to redistribute them with the
  public ODS project. This records the contributor's authorization; it does not
  declare the artwork public domain or override any applicable copyright.
- Pixel's mascot renderer and workbench adaptation come from the Osmantic Pixel
  project. Retain the upstream attribution and license notices in
  `../../../docs/pixel/upstream/`. Pixel's terms remain distinct from the ODS
  license; this contribution does not relicense upstream material.
- Third-party JavaScript packages retain their own licenses. Dependency versions
  and integrity hashes are recorded in `package-lock.json`.

No external image or font service is contacted to render the themes or profile.
Profile photos are cropped/resized in the browser and saved in that browser's
local storage; they are not uploaded to the model or a remote image service.
