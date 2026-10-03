# ConvertX

Local batch conversion using the upstream converter selection UI: images, audio/video, documents, ebooks, structured data and some 3D asset formats. The application selects from bundled tools such as FFmpeg, LibreOffice, Pandoc and Assimp. Available input/output pairs depend on the converter; not every file can be converted to every advertised format.

## Packaging and account setup

The official v0.18.0 image is pinned by digest. ConvertX is AGPL-3.0; bundled converter programs retain their own licenses. Original Bun startup is retained. The image's MarkItDown pipx environment remains at its original absolute path with read/execute access for UID 1000; application data and writable home live under `/app/data`.

Set `CONVERTX_JWT_SECRET` to a persistent random signing secret. Open `http://localhost:11085` (or `CONVERTX_PORT`) and create the first account. Upstream permits that initial setup even with subsequent registration disabled. Login remains required; anonymous conversions and shared anonymous history are disabled.

HTTP cookies are explicitly permitted for this loopback-only deployment. Other containers on `ods-network` can reach `http://convertx:3000`. A remote deployment needs HTTPS and corresponding cookie configuration; the recipe does not expose a LAN port or create a public URL.

## Working with project assets

Upload selected files, choose the supported output format/converter, run the conversion and download results back to the intended Playground project. Inspect output quality, dimensions, layout or model geometry before replacing source assets. Originals in the project are not mounted or modified automatically. Installation does not create a Portal API adapter or give the model access to uploads.

The `convertx-data` volume retains the SQLite account/job database, uploads, output directories and writable tool profiles. **Upstream cleanup runs on startup and every 24 hours, deleting jobs/files older than 24 hours.** This is temporary conversion storage, not an archive; download results promptly. Back up account data with the service stopped if needed. Preserving the volume does not disable scheduled cleanup.

## Resources and verification

Two concurrent conversions, two CPUs and 3 GiB RAM bound the default workload. Large video/documents may exceed those limits; conversions also require temporary disk space. No host device, GPU passthrough, FFmpeg hardware argument or external model provider is configured. The bundled application accepts large uploads; local disk availability remains an operational limit.

The official image supports Linux amd64/arm64 for Docker on Windows, Linux and macOS. Image build, first login, converters under the nonroot account, cleanup and output fidelity remain runtime-pending. `/healthcheck` verifies the web service only, not successful operation of every bundled converter.

Upstream: https://github.com/C4illin/ConvertX/tree/v0.18.0
