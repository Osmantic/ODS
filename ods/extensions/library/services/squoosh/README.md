# Squoosh

Compare image quality and file size, resize images and compress into supported formats using browser-side WebAssembly codecs. This is the actual GoogleChromeLabs Squoosh UI, built from pinned source, rather than an ODS imitation.

## Use with a project

Open `http://localhost:11092`, choose an image, compare the encoding options and download the result. Save the downloaded image into the intended Playground project's assets explicitly. No project directory is mounted or rewritten and Portal does not upload images automatically.

Image processing occurs in the browser. The static container serves the application/codecs; it has no upload endpoint, processing API or account system. Browser memory and CPU determine feasible image sizes. GPU/model settings are untouched. The container's resource limit applies only to the static server, not browser allocations.

## Local privacy and browser requirements

Upstream's Analytics bootstrap is removed during the build with a source-shape check; its remaining event calls do not enqueue or transmit analytics. A same-origin connection policy also prevents external browser fetches. The upstream codec licenses and application license remain distributed with the built source/assets.

COOP/COEP headers preserve cross-origin isolation needed by threaded WebAssembly. Use a current browser and the dedicated localhost origin. Opening inside an iframe or through a proxy that strips isolation headers can disable codecs/features. Secure-context features work on localhost; remote access requires appropriate HTTPS and header configuration. No TLS certificate is installed automatically.

There is no server-side image library or data volume. Download results before clearing browser storage. Service-worker/PWA caches are tied to the browser origin; refresh or clear this site's storage if old application assets remain after an update. No offline behavior is claimed before an actual browser check.

## Packaging and verification

Apache-2.0 upstream source commit `e8d35e0fb66eb16eff6fe8fc773eabcbb7128de3` (2024-08-19), with archive checksum, dependency lockfile and pinned Node/nginx build/runtime images. This is not presented as a newly maintained 2026 project. Browser codecs are bundled by upstream; no inference model or third-party processing service is required.

The static runtime is nonroot and read-only with temporary nginx files, and supports Docker Linux containers on amd64/arm64, including Windows/macOS hosts. The patched source compiled successfully in an isolated Ubuntu WSL directory with Node 26.8.1 after a clean lockfile install. That checks the source transformation and static build, not the pinned Node 24 Docker build. Image and browser checks—including codecs, CSP compatibility, download fidelity and service-worker updates—remain pending. No service or model was started.

Source: [official repository](https://github.com/GoogleChromeLabs/squoosh/tree/e8d35e0fb66eb16eff6fe8fc773eabcbb7128de3).
