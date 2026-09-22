# miniPaint for ODS

Layered raster-image editing for project artwork, screenshots and textures. Source: https://github.com/viliusle/miniPaint at `a79733eb803fc97084ef0ee4faa96b031e69e1c0`, package version 4.14.3. The actual MIT license text was inspected; GitHub's unclassified license metadata is not the license itself.

## Install and launch

Install `minipaint` from the ODS library and open its launch link, normally http://localhost:11078/. Configure `MINIPAINT_PORT` if necessary. No account, API key, inference model or GPU container is needed.

The first build downloads a checksum-pinned source archive, installs the npm lockfile and compiles the production bundle. Git is present only in the build stage for the locked Git dependency. The runtime keeps upstream image assets, source asset paths and GIF worker alongside the compiled bundle; copying just bundle.js would break some exports. Nginx runs as UID 101 with a read-only filesystem and temporary working files.

## Workflow with Portal

Import an image from the intended `Playground/<project>`, edit its layers, crop/resize or apply the required adjustments. Save an editable JSON project to retain layer data, and export PNG/JPEG/WebP or another supported format for the target application. Hand those real exported files back to Portal through the normal workspace file workflow. A flattened PNG does not replace the layered source.

Editing occurs in browser memory; quicksave uses browser localStorage. There is no server document database or shared project-volume mount. Download your source before clearing browser data, switching profiles/origins or exceeding browser storage limits. Large images and many layers can consume significant client RAM independently of the small static server limit.

## Platforms and network behavior

The server base supports Linux amd64/arm64 for corresponding ODS Docker adapters on Windows, Linux and macOS. Image processing runs in the client browser; clipboard, file-picker and export behavior can differ by browser. No CUDA backend or chat-model context is changed.

Local image editing does not need a remote image service. Optional opening of URLs and font/network features can contact their specified origins; cross-origin image permissions can affect export. This recipe configures no upload endpoint, remote account or proxy to bypass those restrictions. The upstream license is served at `/MIT-LICENSE.txt`, with bundled source/dependency notices retained.

## Validation status

Build, browser editing, layered JSON recovery and animated GIF export remain pending for user testing. Configuration/staging checks do not prove those operations work. HTTP health verifies only the served editor page. A useful manual check is to import an image, add a layer, save/reopen JSON, and export a PNG with the expected transparency.
