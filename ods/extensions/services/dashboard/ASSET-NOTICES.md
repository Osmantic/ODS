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

## AI-generated circular wallpapers

The blue, red and purple themes now use `ods-blue-circular.png`,
`ods-red-circular.png` and `ods-purple-circular.png`. These 1254 x 1254 PNGs
were generated with the built-in image generation tool on September 23, 2026,
using contributor-provided images as visual references and changing their
geometry to circular surfaces. The previous three JPEG assets are removed.
AI generation and a geometry change do not by themselves establish copyright
clearance; these files must not be described as independently rights-cleared.

## Verified wallpaper source: Forest

- File: `src/assets/wallpapers/luisdelrio.jpg` (Forest theme).
- Photographer: Luis Del Río Camacho (`@luisdelrio`).
- Original publication: [person in, Inverness](https://unsplash.com/photos/person-in-nadEf7Yjb_Q),
  published November 3, 2015.
- License: [Unsplash License](https://unsplash.com/license), verified September 23, 2026.
  The source page explicitly identifies this as a free photograph under that license,
  rather than an Unsplash+ asset.
- The license permits downloading, copying, modifying, distributing and using the
  image, including commercially. Attribution is appreciated but not required.
  It prohibits selling the image without significant modification and compiling
  images to replicate a similar or competing service. This image remains under
  its own license; the ODS software license does not relicense it.
- Suggested credit: Photo by Luis Del Río Camacho on Unsplash.
- SHA-256 of the bundled derivative:
  `04c7e948ebcef3ce5ea5cc8ffce8bfd1ed1e61f65dd0637ed54428deb18b6de6`.

This verification applies only to the named file. The collection-level contributor
statement above is not independent rights evidence for the other wallpapers;
their individual source and redistribution permissions remain under review.
