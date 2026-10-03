# SVG-Edit for ODS

Vector drawing and SVG editing in the browser. Upstream: https://github.com/SVG-Edit/svgedit, MIT. This recipe pins commit `c44f061d2f9a626d2771cc931298af5a45522d87` from 2026-08-05 (package version 7.4.2), rather than presenting the 2023 GitHub release v.7.3.3 as recent.

## Installation and launch

Install `svgedit` from the ODS extension library. Its launch link defaults to http://localhost:11077/; change `SVGEDIT_PORT` if needed. No GPU runtime, model, external account or API key is required.

The first installation builds the pinned source with its npm lockfile, including the svgcanvas workspace and the upstream postbuild steps that copy editor assets and bundle extensions. The resulting `dist/editor` is served by nonroot Nginx, with a read-only filesystem and temporary working directory. Browser-test downloads are disabled. The optional cross-domain example, alternate IIFE entry and test harness are not published by this recipe; the normal editor and its bundled editing extensions remain available.

## Use with a Portal project

Open or import the project's SVG, adjust paths, layers, shapes and typography, then save/export the SVG back through the normal project-file workflow into `Playground/<project>`. Keep the editable SVG in version control and let Portal reference that actual file in HTML, CSS or the chosen application framework. Exporting a raster image loses vector editability; retain the SVG source as well.

The browser does not share the Portal filesystem automatically. File import/export is explicit, and this recipe does not pretend a Docker volume contains browser drawings. Browser preferences/storage are origin-specific; save files before clearing storage or switching browser, profile, host or port. Embedded/external image references and unavailable fonts can affect a document's portability; inspect the exported asset in the target project.

## Platform and networking

The pinned static-server images provide Linux amd64/arm64 builds for the respective ODS Docker adapters on Windows, Linux and macOS. Drawing is performed by the client browser. Recent browsers are expected; actual compatibility and performance still require user testing. This is a vector editor, distinct from the diagram-layout tools and 3D model editors in the catalog.

The recipe installs no remote storage provider, analytics account or additional editor plugin. Upstream optional extensions and user-opened document references may use external resources. Only the bundled editor is included; third-party extensions retain their own licenses. License, author and dependency-license metadata are retained under `/licenses/`.

## Verification boundary

Schema, Compose and staging checks do not verify editing. Image build, import/export and persistence remain pending for user testing. HTTP health only verifies the editor page is served. A useful manual check is to import an SVG with paths and text, edit a path, export it, reopen it, and use it in the intended website.
