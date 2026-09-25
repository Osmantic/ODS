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

## Locator matching

A CSS locator matches through the isolated world's `querySelectorAll`, hidden
elements included. An exact role/name locator for `assert-visible` or `click`
matches Chromium's accessibility tree, which contains only rendered elements.

That tree omits hidden elements and computes no name for them, so for
`assert-hidden` an exact role/name locator also matches hidden elements. The
same role/name can therefore be asserted hidden, clicked into view, and asserted
visible. The hidden-inclusive match follows Playwright's
`getByRole(role, {name, exact: true, includeHidden: true})` role and accessible
name rules; script, style, template, and noscript text never contributes.
Names compare after whitespace is collapsed, case-sensitively. It runs in the
isolated world. Chromium's rendered matches are kept and the union is
de-duplicated by element identity, so a rendered element is matched exactly as
for the other actions, and uniqueness counts rendered and hidden matches
together.

A step whose locator matches no element fails as `no_match` with
`before: {count: 0}`. A step whose locator matches several elements fails as
`selector_not_unique` with that count. Capsules built before `no_match` report
zero matches as `selector_not_unique`, and callers accept both. Neither is
evidence about visibility: the caller must correct the locator and retry on the
same snapshot, not change the site to satisfy a locator.

## Evidence scope

The receipt binds the exact site, full snapshot hash, viewport, and canonical
UTF-8 JSON request hash (`planSha256`). Each executed step records its index,
locator, CSS visibility measurement, stable sampling, status, and (for clicks)
after measurement. Remaining steps are not executed after a failed assertion.
A click dispatch alone proves no resulting behavior. Verify the requested
initial state, click, and resulting condition with separate assertions.

A Chromium CSS parser `SyntaxError` produces an `invalid_selector` failed step
bound to the exact submitted locator and index. That step has `stable: false`
and no `before` or `after` measurement: invalid syntax does not establish a
missing or hidden element. Earlier steps remain in the receipt and later steps
are not executed. The caller must correct the CSS or use a supported exact
role/name locator while retaining the requested interaction checks. Selectors
are never translated or relaxed. Other browser failures remain unavailable.

Visibility means CSS layout visibility, including opacity and ancestor CSS
visibility. It does not prove pixel paint, clipping, occlusion, or a full
accessibility audit. Exact role/name locators do check that accessible match.
Measurements run in an isolated Chromium world so page script cannot replace
the measurement APIs. Visible elements bearing `hidden` produce a diagnostic;
they fail only an explicit hidden assertion. Intentional CSS overrides are not
rewritten or rejected at publication.

Stable sampling requires two identical observations 100 ms apart. If they
disagree, sampling continues for at most 1.5 seconds per observation, allowing
ordinary finite transitions to finish. Persistent disagreement fails as
`unstable`; timeout never substitutes for a visibility verdict. The page's
animations and styles are never paused, sought, or changed. This is sampled
stability, not proof of continuous visibility between or after observations.
The overall 45-second capsule deadline remains unchanged.

## Page script errors

The capsule records uncaught exceptions raised by the inspected page (the
Playwright `pageerror` event on the inspection page, registered before
navigation) from startup until the receipt is built. The fixed wrapper document
contains no script, so page script exceptions come only from the sandboxed
preview frame or a frame it created. The listener is page-scoped; blocked
popups are never observed. Console messages are not recorded: they are
author-controlled logging, and blocked resources already appear in
`blockedRequests`.

Browser automation also injects URL-less scripts into every document.
Playwright's service-worker block reads `navigator.serviceWorker`, which throws
a `SecurityError` in every opaque-origin preview frame. An exception whose
entire stack lies in URL-less anonymous code is therefore not attributed to the
page. Page code always runs from its document or script URL, and the CSP
forbids string evaluation; a top-level page exception with no stack frames is
attributed. The service-worker block itself is unchanged.

When at least one exception was observed, the receipt carries
`pageErrors: {count, messages}`. `count` is 1–1000 and saturates at 1000.
`messages` holds one to three distinct messages in first-seen order, each
`name: message`. Control, format, private-use, surrogate, unassigned, and line
or paragraph separator code points become spaces; whitespace is collapsed; a
message longer than 200 characters is cut to 199 plus `…`. The field is absent
when nothing was observed and in receipts from capsules built before it existed.
Callers validate these exact bounds and reject any other shape.

Message text is author-controlled data. Callers present it only as quoted,
untrusted page output and never follow it. Page errors change neither step
status nor receipt `status`, and never block publication. A receipt carrying
`pageErrors` is not verified interaction evidence: callers report the
interactions as unverified and direct a script repair, republication, and a
fresh inspection. An author handler that cancels the error event (for example
`window.onerror` returning `true`) suppresses the report, so absence is not
proof that no script failed.

The capsule is baked into the locally built inspection image. A host keeps
producing receipts without `pageErrors` until the installer rebuilds that image.

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
Unicode hash compatibility, forged/incomplete receipts, unavailable results, and
page-error bounds and presentation.
`python3 -m unittest discover -s tests -p test_preview_inspection.py` checks
protocol, ownership, paths, immutable bytes, subprocess bounds, cleanup, and
page-error receipt shaping through a scripted browser double.
Set `ODS_PREVIEW_BROWSER_TESTS=1` only for the fixture Chromium suite; it also
checks the hidden-inclusive role/name matcher against Playwright's own
`includeHidden` engine. Set
`ODS_INSPECTION_TEST_IMAGE=sha256:<candidate>` for real isolated-container
smoke, observed hidden-flex regression, hung-script, and cancellation cleanup.
These test-only variables never select a production image or grant authority.
