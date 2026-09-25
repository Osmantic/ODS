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

## Rendered colors

Local models are often not multimodal, and CSS source alone does not show what
is painted: an accent variable used only by `:focus` outlines changes nothing
visible. Publication is byte-only and renders nothing, so the capsule reports
the painted palette with every receipt it builds.

After the step context is closed and its receipt is final, the capsule loads
the same snapshot once more in a separate context of the same browser: a fixed
1280x720 desktop viewport, default (light) color scheme, device scale 0.25.
It uses the same loopback server, request guard, navigation limit, and popup,
download and websocket blocking, and registers no page-error listener. It
waits 100 ms after `load`, takes a 320x180 PNG screenshot of the viewport (one
device pixel per 4x4 CSS pixels) and decodes it with the standard library. The
page as first loaded is captured, never the state left by the inspected
clicks, and never hover or focus styles.

Each pixel's exact color goes to one fixed bucket. A color with chroma under
26/255 is neutral and named by HSL lightness: `black` (<0.13), `dark gray`
(<0.40), `gray` (<0.72), `light gray` (<0.94) or `white`. Otherwise the HSL hue
names the family: `red` (<12 or >=345 degrees), `orange` (<36), `amber` (<50),
`yellow` (<68), `green` (<165), `teal` (<195), `blue` (<255), `purple` (<290),
`pink` (<345). Red with lightness >=0.75 is `pink`; hues 12-50 that are dark
(lightness <0.35) or muted (saturation <0.5 and lightness <0.7) are `brown`.
Chromatic buckets split into three lightness bands (<0.35, <0.65, rest).

The receipt carries `renderedColors: {viewport: {width, height}, colors}`.
`colors` lists at most six buckets by pixel count, largest first; ties order
by bucket name and band. Each entry is `{name, hex, percent}`: `hex` is the
most frequent exact color in the bucket (lowercase `#rrggbb`, ties to the
lowest), and `percent` is the bucket's share of the viewport rounded half up.
Buckets rounding below 1% are not listed, so the rounded shares can exceed
100 by at most one half per entry. Callers validate these exact bounds and
reject any other shape.

The palette is evidence about paint only. It never changes a step or the
receipt status, and it does not prove any requested behavior. A failed or
blocked capture omits the field, as do receipts from capsules built before it.
The tool states it once, in a fixed line after the assertion scope, and omits
it from the quoted evidence copy:

`Rendered colors (by area, desktop 1280x720 as loaded): white 77%, green
#2d5a3d 12%, green #4a7c59 7%, light gray 3%, gray 1%. Colors under 1% of the
view and hover/focus-only styles are not listed.`

Neutral names carry no hex. The line is part of the inspection tool result
only, never the system prompt. The capture adds about 0.2 seconds to an
inspection. A host keeps producing receipts without `renderedColors` until the
installer rebuilds the inspection image.

## Requested text

The plugin may add `texts` to a request: 1–12 distinct strings, each at most
120 Unicode characters / 480 UTF-8 bytes, without controls or surrounding
whitespace, all inside the 8 KiB request limit. They are the owner's requested
page text, never model input. `planSha256` covers the plan without `texts`, so
a plan hashes the same with or without them; the receipt echoes each text
instead. The publisher's snapshot export never receives them. A broker built
before this field rejects it before any browser runs, with an unbound
`unavailable` failure, and the plugin then repeats the same plan once without
it.

After the palette, when `texts` is present and the capsule is less than 30
seconds in, the capsule loads the snapshot once more in its own context: the
fixed 1280x720 desktop viewport, the same server, request guard and blocking,
no page-error listener. 100 ms after `load` one isolated-world measurement
looks for each text as rendered text anywhere on the page (text nodes and
button input values, folded like the plugin's `canonicalText`, caselessly;
script, style, form-option, media and embedded-frame content never counts).
Each deepest element containing it is judged: `display-none` (the element or
an ancestor), `visibility-hidden`, `content-hidden` (closed `details` or
`content-visibility: hidden`), `transparent` (opacity at or below 5% through
its ancestors), `zero-size`, `clipped` (overflow `hidden`/`clip`, `clip` or an
`inset()` clip path; a scroll container's content counts as reachable),
`off-page` (outside the page's scrollable area, as moved off-canvas),
`transparent-text`, or `same-color` (text color within a 1.05 contrast ratio of
a backdrop made only of solid ancestor backgrounds, with nothing else painted
there). Gradient-clipped, stroked and shadowed text, backgrounds with images,
filters or blending, and dark color schemes without a background are never
judged by color. The page's own position below the first viewport is not a
reason.

When every located text is visible, that single measurement decides. Otherwise
the page is scrolled through once (instant scrolls of 80% of the view, at most
12 steps and 3 seconds, 150 ms apart), then allowed 400 ms for finite
transitions, then returned to the top, measuring only the texts still hidden.
A text seen visible in any sample is visible, so scroll-triggered reveals and
load animations pass. Page styles and animations are never changed.

The receipt carries `requestedText: {viewport, scrolled, texts}` with one entry
per sent text, in order: `{text, status}` for `visible`, `absent` (not found as
rendered text) or `unmeasured` (past the bounds of 20,000 elements or 4 million
characters), and for `hidden` also `element`, `reason`, optional `culprit` (the
element that hides it, when not the element itself) and, for `same-color`
only, `colors: [text, background]`. Element names are a tag, then an id or up
to two classes, limited to `[A-Za-z0-9_-]`: page data, never instructions.
Only `hidden` is ever reported to the model. The field never changes a step,
the receipt status or interaction proof, and is omitted when the check fails,
is blocked, or starts late.

Once the step receipt is final, the capsule keeps a copy. If the palette or
requested-text load stalls (a page script that never yields blocks every
isolated-world call), the capsule writes that final receipt without the
unfinished evidence 12 seconds later, and never later than 40 seconds after it
started, then exits.

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
Unicode hash compatibility, forged/incomplete receipts, unavailable results,
page-error bounds and presentation, rendered-color bounds and the fixed
line, and requested-text bounds, echo, presentation and the older-broker retry.
`python3 -m unittest discover -s tests -p test_preview_inspection.py` checks
protocol, ownership, paths, immutable bytes, subprocess bounds, cleanup,
page-error receipt shaping through a scripted browser double, the PNG decoder
and palette buckets, the separate palette capture, the requested-text scroll
pass, and the stalled-evidence deadline.
Set `ODS_PREVIEW_BROWSER_TESTS=1` only for the fixture Chromium suite; it also
checks the hidden-inclusive role/name matcher against Playwright's own
`includeHidden` engine, replays the fleet round 069 page
(`tests/fixtures/preview-palette/tower1-r069`) and its amber repair, and
replays the fleet round 087 page
(`tests/fixtures/preview-requested-text/tower1-r087`) and its footer repair. Set
`ODS_INSPECTION_TEST_IMAGE=sha256:<candidate>` for real isolated-container
smoke, observed hidden-flex regression, hung-script, and cancellation cleanup.
These test-only variables never select a production image or grant authority.
