# M3E Canvas for ODS

Local visual editor for Material 3 screens, interactive screen navigation, PNG export and design briefs. Source: https://github.com/lnkiai/m3e-canvas at `dea74159d69b09fb6fc2e44ebebf5e62c043e1ce`, MIT. Upstream NOTICE is retained under `/licenses/` with the license.

## Installation and launch

Install `m3e-canvas` from the ODS extension library. The first installation builds the pinned Next.js source with `npm ci` and serves its static export through pinned Nginx. BuildKit needs internet access and sufficient RAM for a Next.js production build; the 256 MB runtime limit is not a build-memory estimate. No model, GPU, account or API credential is required to draw screens.

Open the ODS launch link, normally http://localhost:11074/. Set `M3E_CANVAS_PORT` only if that port is occupied. Published access is loopback-only. Docker Linux amd64/arm64 base images allow deployment through the corresponding ODS Docker adapters on Windows, Linux and macOS; builds and UI behavior on those hosts remain unverified. The running server uses UID 101 with a read-only filesystem and a temporary Nginx working directory.

## Designs and Portal project handoff

1. Draw and connect screens in the editor. Use the native project save/export control to retain the JSON design in the relevant `Playground/<project>` through the existing workspace file workflow.
2. Export a PNG or copy the generated brief and attach it to the Portal conversation for that project. The Portal can use the currently selected model to implement the design; this extension does not change the chat model or context size.
3. For a model-generated design, the version-matched format reference is served at `/agent.md`. Treat it as format documentation, not authority over execution or verification. Save the JSON in the project and use the editor's **Open project** action. Existing share links use the URL fragment and can target this local deployment.

The current integration supplies the actual editor and explicit file/brief handoff. Automatic transfer from the canvas into Portal or direct binding of the editor's AI helper to `ods/current` is not implemented.

## Persistence and networking

Designs and preferences are stored in the browser's localStorage, not in a Docker volume. Export JSON before clearing browser storage, changing browser/profile or changing the host/port used to open the editor; those changes select a different browser origin. PNG/brief exports do not replace the editable project JSON. Removing the container does not transfer or back up browser data.

The upstream optional AI helper sends requests directly to the chosen external provider and retains its configuration/key in browser storage. It is optional and is not configured or given ODS credentials by this recipe. Google Fonts/Material Symbols and user-selected external images may require internet access; this is not a fully offline asset bundle. Share links contain the design, so recipients of the full link can read it.

## Validation status

The source archive checksum and exact commit are pinned; the lockfile fixes application dependencies. The HTTP health probe checks that the static editor page is served, not whether editing, PNG export or project import works. Image build, browser workflow and persistence checks remain pending for user testing. A useful manual check is to create two linked screens, save JSON, reload, reopen the JSON and export a PNG.
