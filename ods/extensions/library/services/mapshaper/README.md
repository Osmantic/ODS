# Mapshaper for ODS

Local geographic-data editing and conversion, pinned to v0.7.62, commit `629049657b54242552d3488b39e75b3a78cd68d2`: https://github.com/mbloch/mapshaper. MPL-2.0.

## Install and launch

Install `mapshaper` from the ODS library and open its launch link, normally http://localhost:11080/. `MAPSHAPER_PORT` selects another loopback port. No AI model, GPU runtime, account or API key is required. The first image build installs the locked npm dependencies, generates the complete web bundles and WebAssembly dependencies, and builds the matching documentation. Runtime is an unprivileged static Nginx server with a read-only filesystem.

## Project use

Import the intended dataset, inspect its layers and attributes, simplify boundaries or apply the needed editing commands, and export the result to `Playground/<project>` using the normal file workflow. Preserve the original dataset separately. For Shapefiles, import the related `.shp`, `.dbf`, `.shx` and projection files together, or their archive, rather than discarding attributes or coordinate metadata.

Select the output format and coordinate reference system required by the target mapping application. Simplification trades detail for file size; review small features and topology before replacing a project's map. Portal can use the actual exported GeoJSON/TopoJSON or other supported output as a project asset. This recipe supplies the browser editor and its built-in command console, not a server execution API or an installed host CLI.

Version-matched documentation is local at `/docs/`, with machine-readable documentation at `/llms.txt` and `/llms-full.txt`. Those documents describe formats and commands; they do not grant arbitrary execution authority to an agent.

## Storage, platforms and optional services

Datasets are processed in browser memory and saved by explicit export. Browser preferences are origin-specific; there is no server dataset database or implicit Portal workspace mount. Large geometry/raster datasets can exceed client memory, regardless of the 256 MB static-server runtime limit. Save output before clearing storage or changing browsers.

Linux amd64/arm64 base images cover the corresponding ODS Docker adapters on Windows, Linux and macOS. Processing depends on the client browser's JavaScript/WebAssembly support. Actual host/browser combinations remain unverified.

The upstream Mapbox basemap configuration is replaced with `window.mapboxParams = null`, using the application's existing no-basemap mode. Editing imported datasets remains available; commercial basemaps, satellite imagery and the upstream site's keys are not activated. User-requested remote file imports can still contact their specified sources. No external model or context setting is changed.

## Source and validation

The exact upstream archive is served at `/source/mapshaper-0.7.62.tar.gz`, the ODS packaging and basemap configuration under `/source/ods-recipe/`, and the license at `/LICENSE`. Preserve corresponding source and dependency notices when redistributing modifications.

Image build, import/export, topology results and browser performance remain pending for user testing. Schema/Compose/staging checks validate packaging only; HTTP health confirms that the editor page is served. A useful manual check is to import a small GeoJSON, simplify it, export it and verify its coordinates and attributes in the target application.
