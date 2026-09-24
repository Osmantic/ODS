# Snapshot-bound preview inspection protocol

`pixel_ods_workspace_preview_inspect` is optional. Registration and completion
requirements must be enabled only when the protected inspector is installed.
Publishing remains static, create-only byte publication; it gains no Docker
socket or browser authority.

## Request

The plugin sends one JSON object with `schemaVersion: 1`, `action: "inspect"`,
`siteId`, the full snapshot `sha256`, `viewport: {width, height}`, and `steps`.
Each step has `action` (`assert-visible`, `assert-hidden`, or `click`) and
`locator`. A locator is either `{selector: "CSS"}` or
`{role: "button", name: "Show items", exact: true}`. Semantic roles are
allowlisted. There are no URLs, executable expressions, shell commands, image
names, host paths, or Docker arguments in this protocol.

Requests are limited to 8 KiB, 12 steps, viewport dimensions 240–1920 pixels,
256 Unicode characters / 1024 UTF-8 bytes per CSS selector, and 120 characters /
480 bytes per accessible name. Controls and formatting controls are rejected.
Every locator must match exactly one element, including hidden assertions.
Use CSS to identify a hidden element excluded from the accessibility tree.

## Evidence scope

The receipt binds the exact site, full snapshot hash, viewport, and canonical
UTF-8 JSON request hash (`planSha256`). Each executed step records its index,
locator, CSS visibility measurement, stable sampling, status, and (for clicks)
after measurement. Remaining steps are not executed after a failed assertion.
A click dispatch alone proves no resulting behavior. Verify the requested
initial state, click, and resulting condition with separate assertions.

Visibility means CSS layout visibility, including opacity and ancestor CSS
visibility. It does not prove pixel paint, clipping, occlusion, or a full
accessibility audit. Exact role/name locators do check that accessible match.
Measurements run in an isolated Chromium world so page script cannot replace
the measurement APIs. Visible elements bearing `hidden` produce a diagnostic;
they fail only an explicit hidden assertion. Intentional CSS overrides are not
rewritten or rejected at publication.

## Host custody and isolation

Linux/WSL uses `/run/ods-pixel-inspection/control.sock`, a root-controlled 0750
parent with a 0660 group-connectable socket. The kernel peer UID must match the
configured owner. Group write permission on the parent is forbidden: it would
allow replacing the socket and forging receipts. The broker can use the
minimum read capability to traverse the owner's protected immutable snapshots.

Native macOS invokes the fixed protected helper via `/usr/bin/python3`.
Configuration is root-owned, not group/other writable, at
`/etc/ods-pixel-inspection.json` (Linux) or
`/usr/local/libexec/ods-pixel-services/helpers/preview-inspection.json` (Mac).
Exact fields: `imageId`, `docker`, `snapshotRoot`, `ownerUid`, `transport`.
The image is an installed immutable `sha256:` ID; the Docker executable is a
fixed platform allowlist entry and root-owned. The Docker endpoint is explicitly
pinned to `/var/run/docker.sock` or the configured Mac owner's Docker Desktop
socket, never ambient Docker context/environment.

CLI: `serve` accepts authenticated local requests; `request` runs the fixed
native helper request; `health` verifies the installed image identity without
executing a site; `export` reads only `/previews` in the publisher container.
Native export uses fixed `docker exec` argv and actual container UID ownership.
Export grants the publisher no Docker authority. Snapshots are reopened with
the publisher's stable no-symlink file checks and rehashed, then rehashed again
inside the capsule. File count/size caps remain 128 / 4 MiB each / 16 MiB total.
No archive extraction is used.

Every browser run is a fresh Docker capsule: `--pull=never`, `--network=none`,
read-only root, UID/GID 65534, all capabilities dropped, no-new-privileges,
1 CPU, 1 GiB memory, 128 PIDs, private 128 MiB shared memory, and a bounded
256 MiB temporary filesystem. No host mounts, sockets, credentials, or host
URLs enter it. The only stdin is the validated plan and base64 immutable file
map. The loopback server reproduces production iframe sandbox, CSP, MIME/UTF-8,
and CORS/security headers. Foreign requests, extra navigation, popups, and
downloads are blocked; service workers are disabled.

The runner permits at most 45 seconds and 32 KiB output. Cancellation and
termination unwind through exact randomly named container removal. `docker run`
termination alone is not treated as container cleanup. Missing engine, image,
browser, invalid snapshot, or invalid receipt is explicit failed/unverified.
There is no host-browser fallback.

## Tests

`node --test tests/test-preview-inspection.mjs` checks plugin contracts,
Unicode hash compatibility, forged/incomplete receipts, and unavailable results.
`python3 -m unittest discover -s tests -p test_preview_inspection.py` checks
protocol, ownership, paths, immutable bytes, subprocess bounds, and cleanup.
Set `ODS_PREVIEW_BROWSER_TESTS=1` only for the fixture Chromium suite. Set
`ODS_INSPECTION_TEST_IMAGE=sha256:<candidate>` for real isolated-container
smoke, observed hidden-flex regression, hung-script, and cancellation cleanup.
These test-only variables never select a production image or grant authority.
