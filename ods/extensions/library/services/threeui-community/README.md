# ThreeUI Community for ODS

Interactive React/Three.js component catalog and matching source package. Exact upstream commit `68802d5428071ada5c20db8094b1649e6bb770ed`, package version 1.2.0: https://github.com/MengTo/threeui.

## Install and use

Install `threeui-community` through the ODS library and open its launch link, normally http://localhost:11075/. `THREEUI_COMMUNITY_PORT` changes the published loopback port. No account, API key or model is needed. The first installation builds the pinned source and lockfile, including upstream public-boundary checks, the site and React library. Build memory needs exceed the 256 MB static-server runtime limit.

Browse a component, exercise its controls and use the source tab to inspect its implementation. This extension also serves the matching source-built npm archive at `/packages/threeui-community-1.2.0.tgz`. Download it to the intended project's vendor directory through the normal workspace workflow. From that project directory, install the saved archive with its package manager, for example `npm install ./vendor/threeui-community-1.2.0.tgz`; retain the lockfile. Do not overwrite unrelated project dependencies or install it globally.

React and React DOM must satisfy the package peer range (18 or 19); Three.js must satisfy its declared range. Import a selected component through its exported subpath and import `@designcodeio/threeui/style.css`. Some full-document components need assets from `node_modules/@designcodeio/threeui/lib-dist/assets/` copied to the application's public directory, or their documented `sourceUrl`/`assetBaseUrl` override. Inspect the selected component's source and requirements; copying only JSX is insufficient for those components.

The local package can also be retrieved through the Docker service address `http://threeui-community:8080/packages/threeui-community-1.2.0.tgz` by an authorized workspace tool on the same ODS network. This is a download endpoint, not an automatic shared project mount. Automatic chat-driven project dependency installation is part of the broader unfinished ODS extension workflow.

## Licensing, storage and hardware

Only the upstream Community release is packaged. MIT notices for code and included authored assets, OFL font notices and third-party notices are retained under `/licenses/` and in the package. Pro/Beta implementations are excluded by upstream's public-boundary build checks. The upstream Get Pro link remains an external offer; it does not grant access through ODS.

Remote preview media and some scene CDN resources remain external references and are not copied into this recipe or represented as MIT-licensed assets. The catalog is locally served but not guaranteed fully offline. Check external dependencies of each selected scene before redistributing a derived project.

Theme/preferences live in browser localStorage. Project source belongs in Playground and project version control, not an invented server data volume. Changing browser origin or clearing storage resets local preferences. The server runs as UID 101, read-only, with temporary Nginx files.

Linux amd64/arm64 base images support the respective ODS Docker host adapters on Windows, Linux and macOS. Rendering happens in the client browser: individual Three.js scenes require suitable WebGL/browser support and performance depends on the client GPU. No CUDA runtime or model context is configured.

## Verification boundary

Schema, Compose and installation staging checks do not prove runtime correctness. Image build, browser rendering, package import, asset paths and persistence remain pending for user testing. HTTP health checks only confirm that the catalog page is served. A useful manual check is to open a component directly by its route, inspect its code, download the local package, and render that component with its assets in a compatible project.
