# JSON Crack for ODS

Apache-2.0 JSON graph editor built from commit
`a46d0d0adb098e12e577a083e87ead7f61f90098`. The source archive and Monaco
0.55.1 archive are checksum-verified; base images are digest-pinned. The native
pnpm workspace is pruned to the web app and built with its frozen lockfile.

## Install and use

Install `jsoncrack` from Extensions. Open `http://localhost:11142/editor`.
`JSONCRACK_PORT` changes the host port. Import a project's JSON file or paste an
API response into the editor, inspect the graph and export the result. Importing
a file does not grant access to its directory or automatically write back to it.
Use the native file export and save into the project explicitly.

The browser performs editing and graph layout. Its native browser storage is
not a server backup: export important documents before clearing site data or
changing origin/port. There is no fictitious document volume or database.
No accounts, collaborative server, commercial ToDiagram features or automatic
Portal project association are provisioned. Native external editor mode is
disabled at build time. The node limit is 10,000, also a build-time setting.

## Resources and networking

CPU-only Linux container on amd64/arm64; use Docker Engine on Linux or Linux
containers in Docker Desktop on Windows/macOS. No GPU or model is required.
Runtime is nonroot with read-only root, bounded temporary space, 256 MB and one
CPU. Graph complexity consumes browser memory, independently of container limits.
Initial compilation needs substantially more memory (allow at least 4 GB) and
network access to source/package registries and Google's build-time font service.

Monaco assets are served locally instead of fetched from unpkg. No Google
Analytics ID is supplied. This is not a claim that every feature is offline:
user-selected URL imports, remote schema requests and external links can access
the network. The HTTP service is loopback-only and has no application login;
configure authentication/TLS separately before exposing it elsewhere.

## Operations and validation

Health checks fetch the real `/editor` export; missing assets return 404 rather
than the home page. Recreate/update the container without losing exported files
on the host; browser data remains tied to the same origin. No application data
is stored inside the disposable container.

Recipe/schema/staging checks do not prove browser behavior. Image compilation,
JSON import/graph/export, local Monaco/worker loading and platform runtime checks
remain pending. No service or local model was started during integration.
