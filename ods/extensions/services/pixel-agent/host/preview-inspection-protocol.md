# Snapshot-bound preview inspection protocol

`pixel_ods_workspace_preview_inspect` is optional. Registration and completion
requirements must be enabled only when the protected inspector is installed.
Publishing remains static, create-only byte publication; it gains no Docker
socket or browser authority.

## Request

The plugin sends one JSON object with `schemaVersion: 1`, `action: "inspect"`,
`siteId`, the full snapshot `sha256`, `viewport: {width, height}`, and `steps`.
Each step has `action` (`assert-visible`, `assert-hidden`, `assert-text`,
`click`, `select-option`, `fill`, `download`, `press`, or `assert-painted`)
and `locator`. `assert-text` adds `expectedText`; `select-option` and `fill`
add `value`; `download` adds `path`, `expectedBytes` and `expectedSha256`;
`press` adds `key` and an optional `holdMs`; `assert-painted` adds nothing
(see the sections below). A locator is either
`{selector: "CSS"}` or `{role: "button", name: "Show items", exact: true}`.
Semantic roles are
allowlisted. There are no URLs, executable expressions, shell commands, image
names, host paths, or Docker arguments in this protocol.

Requests are limited to 8 KiB, 12 steps, viewport dimensions 240–1920 pixels,
256 Unicode characters / 1024 UTF-8 bytes per CSS selector, and 120 characters /
480 bytes per accessible name. Controls and formatting controls are rejected.
Every locator must match exactly one element, including hidden assertions.

## Locator matching

A CSS locator matches through the isolated world's `querySelectorAll`, hidden
elements included. An exact role/name locator for `assert-visible`,
`assert-painted`, `click` or `press` matches only rendered elements:
Chromium's accessibility tree, united by
element identity with Playwright's default
`getByRole(role, {name, exact: true})` role and name rules in the isolated
world. Chromium's names apply CSS `text-transform` (an uppercase-styled
"Show sold out" button is "SHOW SOLD OUT" there); Playwright's names, which the
capsule's own click and the owner's checks use, are the source text. Either
name matches. The rendered-only rules leave out every element hidden for ARIA
(Playwright's `isElementHiddenForAria`: `display: none` or
`aria-hidden="true"` on the element or an ancestor across shadow hosts, an
unslotted shadow-host child, or the element's own `visibility` or
`content-visibility`; opacity does not matter, as in the accessibility tree)
and hidden descendants from a name. Uniqueness counts the union.

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
locator, CSS visibility measurement, stable sampling, status, (for clicks
and key presses) after measurement, and (for `assert-painted`) paint
statistics. Remaining steps are not executed after a failed assertion.
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
accessibility audit; only an explicit `assert-painted` step samples paint, as
aggregate color statistics (see "Element paint" below). Exact role/name
locators do check that accessible match.
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

## Load-time control names

An owner may require a control "named exactly X", and the accessible name is
what assistive technology and exact role/name locators use, not the visible
text. A page script can replace a correct name on load (fleet round 100:
`setAttribute('aria-label', ...)` on the requested button), which neither the
published bytes nor a CSS-selector plan reveals.

After the 100 ms settle that follows `load`, and before any step runs, the
capsule evaluates one read-only function in the isolated world. It walks the
document (and open shadow roots) with the same Playwright-compatible role and
name rules as the hidden-inclusive matcher and records every element whose
role is `button` or `link`, hidden ones included, in document order: its role,
its computed accessible name, whether it is exposed (in the accessibility tree
and rendered with a box; opacity, which entrance animations change at load, is
ignored), what supplied the name (`aria-labelledby`, `aria-label`, `content`,
or `other` such as `title`, `value` or a `<label>`), and its own content text
when that differs from the name. A control in the accessibility tree is named
as Chromium and the fleet's default `getByRole(role, {name, exact: true})` name
it: descendants the tree leaves out (Playwright's hidden-for-ARIA rules:
`display: none`, a non-visible `visibility`, `content-visibility`,
`aria-hidden="true"` on the element or an ancestor) contribute nothing, so an
`aria-hidden` icon or chevron, a hidden alternate label or a `display: none`
badge is no part of the name, unless it is reached through an
`aria-labelledby`, `<label>` or SVG `<title>` reference that is itself hidden.
A control that is itself hidden keeps the hidden-inclusive name that
`getByRole(..., {includeHidden: true})` matches. The own text follows the same
rule. Names and text are author-controlled: whitespace is collapsed, control,
format, private-use, surrogate, unassigned and line or paragraph separator code
points become spaces, and each is cut to 120 characters (119 plus `…`).

The receipt carries `controls: {count, items}`. `count` is the page's total
number of such elements (saturating at 1000); `items` lists at most the first
48, and stops earlier when the encoded items would exceed 6 KiB, so `count`
larger than the list length means the list is incomplete. Each item is exactly
`{role, name, visible, source}` plus `text` when present. The field is absent
when capture fails and in receipts from capsules built before it; it is also
dropped rather than let a receipt reach the 32 KiB output limit. Callers
validate these exact bounds and reject any other shape.

The names are evidence about names only. They never change a step or the
receipt status and are not interaction evidence. The tool omits them from its
quoted evidence copy; when an exact role/name locator matched nothing and a
control of that role shows the locator's name as its text (or differs only in
letter case), the locator feedback states the actual name and what supplied it.
The plugin checks owner-requested control names against them (see
`COMPLETION-RELIABILITY.md`). A host keeps producing receipts without
`controls` until the installer rebuilds the inspection image.

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

The runner permits at most 45 seconds and 32 KiB of receipt output, plus one
bounded JPEG when the request directs a viewport screenshot (see "Viewport
screenshot"). Cancellation and
termination unwind through exact randomly named container removal. `docker run`
termination alone is not treated as container cleanup. Missing engine, image,
browser, invalid snapshot, or invalid receipt is explicit failed/unverified.
There is no host-browser fallback.

## Tests

Native `select-option` adds exactly one `value` string to the step: the exact
option value, at most 256 Unicode characters / 1024 UTF-8 bytes, with no control
characters. Empty values are allowed. The locator must uniquely identify a
visible enabled native single-select containing one enabled matching option.
Multiple selects, custom comboboxes and controls with over 1000 options are
unsupported. Disabled fieldsets and option groups remain disabled. Chromium's
normal selection operation dispatches input/change; isolated-world observations
bind the native selected value and option state before and after. A successful
selection alone does not prove dependent application behavior: assert the
resulting visible text in a subsequent step. Do not redesign the page merely
to accommodate the inspector.

Selection plans use the scope suffix `Native single-select values were observed
only for explicit select-option steps.` Existing plans and scope remain
unchanged. The installer requires the capsule label
`org.osmantic.ods.inspection.select=native-single-select-v1`; before any selection
plan the broker checks the configured immutable image for that capability.
An older capsule returns a bound `unsupported_capability` failure without
executing the plan. Update the plugin, broker/export helper and capsule together;
mixed older installations remain unverified, with no host-browser fallback.

Downloads remain canceled. `blockedRequests: ["download"]` is explicitly an
inspector policy limitation, not proof of a website defect or of handler
completion. Keep the publication unchanged and report the download unverified;
do not retry the same blocked operation. No download bytes or destinations are
accepted by this protocol.

`test_preview_select.py` and `inspection_select.test.mjs` cover exact bounded
values, selection receipts, unsupported old images, disabled/ambiguous controls,
normal input/change events, hostile author prototypes and unchanged download
blocking. The Python suite uses the same opt-in Chromium/Docker settings below.

`node --test tests/test-preview-inspection.mjs` checks plugin contracts,
Unicode hash compatibility, forged/incomplete receipts, unavailable results,
page-error bounds and presentation, rendered-color bounds and the fixed
line, and load-time control-name bounds and locator feedback.
`python3 -m unittest discover -s tests -p test_preview_inspection.py` checks
protocol, ownership, paths, immutable bytes, subprocess bounds, cleanup,
page-error receipt shaping through a scripted browser double, the PNG decoder
and palette buckets, the separate palette capture, and control-name capture
order, sanitization and bounds.
Set `ODS_PREVIEW_BROWSER_TESTS=1` only for the fixture Chromium suite; it also
checks the hidden-inclusive and rendered-only role/name matchers against
Playwright's own `includeHidden` and default `getByRole` engines, and replays
the fleet round 069 page (`tests/fixtures/preview-palette/tower1-r069`) and its
amber repair, the fleet round 100 page
(`tests/fixtures/preview-controls/tower2-r100`), its repair, four variants of
the repaired page whose button carries hidden decorations, and four small pages
for load-time control names, and a page of hidden-descendant,
`aria-labelledby`, `<label>`, SVG `<title>` and hidden-control cases. Each
reported name is checked per element against Playwright's own name: the default
`getByRole` engine for a control in the accessibility tree, `includeHidden` for
a hidden one. It also replays two laptop pages whose uppercase-styled
"Show sold out" buttons Chromium named "SHOW SOLD OUT"
(`tests/fixtures/preview-inspection-laptop-round101.html`,
`tests/fixtures/preview-inspection-laptop-round107-site-1f5f2cf8.html`), and
counts the matchers' style and label reads to keep their work linear. Set
`ODS_INSPECTION_TEST_IMAGE=sha256:<candidate>` for real isolated-container
smoke, observed hidden-flex regression, hung-script, and cancellation cleanup.
These test-only variables never select a production image or grant authority.

## Native text and number input

`fill` takes exactly `action`, `locator`, and `value`: 0–256 printable Unicode
characters, up to 1024 UTF-8 bytes, within the existing 8192-byte request cap.
Use only synthetic test data. One visible, enabled, editable native
`input[type=text]`, `input[type=search]`, `input[type=number]`, or `textarea` is supported. Password,
file and other input types, contenteditable, disabled/readonly fields and
credential/payment autocomplete metadata are refused. Known sensitive field
names are also refused; this is not a claim of universal semantic PII detection.
The isolated page has no user autofill profile, host files or external network.

The native setter runs in Chromium's isolated world and emits `input` and
`change`. This does not simulate keyboard events, trusted typing, focus/blur or
form submission. Follow it with the real page control click and explicit
postcondition assertions. Do not rewrite an interface to accommodate a test.
Existing value content is never included in observations; receipts bind the
requested value and booleans for eligibility/editability/exact value match.
A fill pass proves only the bounded fill operation, not the whole form.

Numbers use a finite decimal or exponent string with a dot separator, such as
`100`, `0`, `.5`, or `-1.25e2`. An empty string clears the field and permits
required-field tests. NaN, infinity (including overflow), commas, whitespace,
hex and incomplete numeric syntax are refused **before** the setter runs with
`numeric_value_required`. No coercion or locale conversion silently changes the
requested value. Exact readback still determines whether the fill passed.

Synthetic values that violate `min`, `max`, `step` or `required` are intentionally
allowed: they are necessary to test the actual page's validation. Numeric
observations add an exact `numeric` object with boolean `syntaxValid`,
`valueMissing`, `rangeUnderflow`, `rangeOverflow`, `stepMismatch` and `badInput`.
These flags neither expose the old value nor claim the form is valid. Use the
page's actual button and assert its visible calculated result or error. CSS
locators work for number fields; no additional accessible roles are introduced.

The immutable capsule must advertise
`org.osmantic.ods.inspection.fill=native-text-number-fill-v2`. The broker checks this
before snapshot reads or execution; mixed old images fail closed with
`unsupported_capability`. Deploy protocol, broker, plugin and rebuilt capsule
together. No network, navigation, file or download permissions are added.
`test_preview_inspection_fill.py` exercises real Chromium form handlers,
selection after filling, typed refusals, old-image rejection and blocked
network effects; `inspection_fill.test.mjs` rejects forged fill receipts.

## Keyboard press

`press` takes exactly `action`, `locator`, `key`, and an optional `holdMs`.
`key` is one of `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `Space`,
`Enter`, `Escape`, `Tab`, a lowercase letter `a`–`z` or a digit `0`–`9`: one
unmodified key, never a modifier, chord, function key or free text. The plugin
schema states this list as an enum rather than a pattern, so local-model
grammar conversion compiles it. `holdMs` is an integer 0–1000 and defaults to
100 when absent: games often set a flag on keydown, clear it on keyup and read
it once per animation frame, so a zero-length press may move nothing.

The locator must match one rendered, visible element, as for `click`: a game
canvas, its container, the page body or a non-text control. The capsule then:

1. refuses the step as `text_field_press_unsupported`, before focusing
   anything, when the target is a `textarea`, a `select`, an `input` other than
   `button`, `checkbox`, `image`, `radio`, `range`, `reset` or `submit`,
   contenteditable content, any element while the document is in design mode,
   or a nested `iframe`, `frame`, `object` or `embed` (whose own focused
   element the capsule cannot observe); otherwise, as a pointer press on the
   target would, it blurs any other element that holds focus (for example a
   button an earlier step clicked), so `Space` on a canvas never activates
   that button;
2. focuses the wrapper's preview iframe, then the target inside it;
3. reads in the isolated world whether the target is the focused element and
   whether the preview document has focus, and records them as
   `focus: {element, document}`. `document.activeElement` stops at a shadow
   host, such as a web component whose inner field takes focus through
   `delegatesFocus`, so the focused element is found by following open shadow
   roots' `activeElement`, here and when blurring in step 1. A canvas without
   `tabindex` cannot take focus, so `element` may be false; the keys then reach
   the document body, and window and document listeners still run. The step
   fails as `focus_failed` when the document has no focus, and as
   `text_field_press_unsupported` when focus landed on an element step 1 would
   refuse or on the host of a closed shadow root, whose focused element the
   isolated world cannot see (CDP `DOM.describeNode` with `pierce` reports
   closed roots), in each case without sending a key;
4. sends Playwright `page.keyboard.press(key, delay=holdMs)`: trusted keydown
   and keyup events, with the key's ordinary default action, to the focused
   frame;
5. observes the target again as `after`, as for a click.

A navigation, popup, websocket or foreign request caused by the key is blocked
and recorded in `blockedRequests`; when it is seen within the step, the step
fails as `press_failed`, exactly as a click fails as `click_failed`. A download
started by a key is `unexpected_download`. A press alone proves no result.
Presses may run in a row (for example `Space` to start, then `ArrowRight`), but
the last one needs an assertion of its visible result before the plan counts
as behavior evidence. A click before a press needs its own assertion first
(click Start, assert the started game, then press); a passing plan without one
says that it does not count. A press plan is never a read-only inspection.

Press and paint plans run only on a capsule labelled for them, so their scope
need not match older capsules. Its base is `Only the listed steps were tested,
each only as stated here. CSS layout visibility, normalized visible-text
assertions and click dispatches do not test pixel paint; no step tests
occlusion, clipping, a full accessibility audit, or overall functionality.`,
which does not deny the presses and paint checks named after it, and next to
presses the fill sentence ends `Fill sends no key events and does not submit
forms.` Other plans keep the exact scope older capsules return. Press plans add
the suffix `Trusted key presses were sent to the focused preview frame only for
explicit press steps; behavior beyond the listed assertions, such as gameplay,
is not verified.` A receipt with the legacy scope never satisfies a press plan.

Security notes:

- Press is not a text path. Fill, with its sensitive-field denylist, remains
  the only way to enter text. Press refuses editable targets and editable
  focused elements, including a field inside an open shadow root and any focus
  hidden in a closed one, and every key comes from the fixed allowlist, never
  from user data. A page's own key handler can still move focus elsewhere
  while the key is down; that page then receives one allowlisted key, inside a
  capsule with no autofill profile, host files or network.
- A trusted key press grants user activation, which can unlock popups,
  downloads, fullscreen, pointer lock and clipboard writes. Popups are closed,
  downloads cancelled and extra navigations blocked; each is recorded in
  `blockedRequests` and fails the receipt. Clipboard and fullscreen stay inside
  the no-network capsule and end with it. CSP `form-action 'none'` blocks a
  submit started by Enter.
- The wrapper document contains no script, and keys go only to the preview
  frame focused in step 2.

The immutable capsule must advertise
`org.osmantic.ods.inspection.press=trusted-key-press-v1`. The broker checks it
before reading the snapshot, and the installer requires it, as for fill and
select. An older image returns a bound `unsupported_capability` failure without
executing the plan; deploy plugin, broker and rebuilt capsule together. Focus
delivery into the sandboxed opaque-origin frame without a prior click is proven
only by the opt-in real-browser cases, which also need a run on the arm64 image
that Windows on Arm hosts build.

`test_preview_inspection_press.py` covers the protocol bounds, the broker's
label check before any snapshot read, the scripted focus and key order and
each refusal, including focus a closed shadow root hides, and (with the opt-in
Chromium or image settings above) a focusable canvas pressed without a prior
click, a window listener behind an unfocusable canvas, `Space` on a canvas
after a button click, a held key read per animation frame, refused text fields,
redirected focus and fields inside open and closed shadow roots that take focus
through `delegatesFocus` or a focus handler, Enter on a link, a key-started
download, and a page error raised in the frame after a key.
`preview_capsule_scripts.test.mjs` runs the refusal and the shadow-root walk
against a minimal DOM. `inspection_press.test.mjs` checks the schema, the
Python/JavaScript key list, scope and plan hash, forged press receipts,
feedback and the evidence policy.

## Element paint

`assert-painted` takes exactly `action` and `locator`. It detects a blank or
single-color render, such as a game canvas that stays black after its start
button or first key press. It is a blank detector, not image recognition.

The locator must match one rendered element, as for `assert-visible`; its
visibility is awaited within the same observation window, and a hidden element
fails as `visibility_mismatch`. Then, sampling every 100 ms within a further
1.5-second window, the capsule:

1. reads in the isolated world the element's content box (border and padding
   removed), its largest corner radii and the frame viewport. The capture
   region is that box inset by 0.3 times the radii, which keeps it inside
   rounded corners, intersected with the viewport, rounded inward to whole CSS
   pixels and inset by one captured pixel, so blended edges of the surrounding
   page never count as a second color. The wrapper iframe fills the
   never-scrolled page from (0, 0), so frame coordinates are page coordinates.
   The sample is empty when less than 8x8 CSS pixels remain, when the region
   is too thin for its length to keep a pixel inside the one-pixel inset once
   its longer side is downscaled to 256 (a fully visible 1000x8 bar, for
   example), or when the element is no longer one rendered match; the
   inspector never scrolls;
2. captures that region with CDP `Page.captureScreenshot` (PNG,
   `captureBeyondViewport: false`), downscaled so neither side exceeds 256
   pixels. Playwright's element screenshot is not used: it scrolls the element
   into view and injects caret-hiding style. The capture is the composited page
   of the step context, so it shows the state after earlier clicks and key
   presses, including anything that covers the element, such as an overlay;
3. decodes the PNG with the standard-library decoder of the rendered palette.

The step passes on the first sample with at least two exact colors. Canvases
often draw on a later animation frame or once their images load, so a
single-color or empty sample is retried until the window ends. A timeout never
passes: the last sample fails the step as `uniform_paint` or, when it was empty,
`not_in_viewport`, whose feedback names all three causes of an empty sample.

A measured step carries `paint: {width, height, distinctColors, dominant:
{hex, percent}, colors, fingerprint}`: the captured size (1–256 pixels per
side), the number of exact colors (saturating at 4096), the most frequent exact
color and its share rounded half up, at most four buckets named as in
`renderedColors`, and the first 16 hex digits of the SHA-256 of the captured PNG
bytes. A `uniform_paint` step carries it with `distinctColors: 1`;
`not_in_viewport` and other failures carry none. A request blocked during
sampling ends the run as `request_blocked`, and a failed capture as
`unavailable`, never as a paint verdict. Callers validate these exact bounds and
reject any other shape.

A pass shows only that the box held two or more colors at one sample. A
gradient background without its sprite, or an invisible hero on a colored
background, still passes; an element's own `clip-path`, mask or rotation shows
the page around it inside its box. Pair it with `assert-text` where the page
shows state as text. When two passed `assert-painted` steps of one locator
around a press or click have the same fingerprint, the tool adds an
informational note that nothing visible changed there at those samples. It
never fails the step: animation makes a changed fingerprint weak evidence of a
working control, and an effect may lie elsewhere or come later.

For the evidence policy `assert-painted` is a postcondition, so a press
followed by `assert-painted` counts as an interaction with its result. It only
observes, so a plan of assertions and `assert-painted` steps stays read-only.
Paint plans use the press and paint scope base (see "Keyboard press") and add
the suffix `Pixel paint was checked only for explicit assert-painted steps, as
aggregate color statistics of each element's on-screen box; this detects a
blank or single-color render, not image recognition or whether the intended
content was drawn.` A receipt with the legacy scope never satisfies a paint
plan.

Security notes:

- No image bytes, base64 data or page text of a paint sample leave the
  capsule: only bounded integers, colors and a truncated hash, which the
  plugin validates exactly. Feedback quotes only the capsule-computed dominant
  color. The only image that ever leaves is the separately directed viewport
  screenshot below.
- The capture reads pixels the no-network capsule already renders and adds no
  network, file or navigation permission. It changes no page style, scroll
  position or animation state. Chromium applies the downscale as capture-time
  emulation; that the page observes no resize, scroll or DOM change during it
  is proven only by the opt-in real-browser case.
- Pure-Python decoding is bounded to 256x256 pixels per sample, so polling fits
  the 1-CPU capsule and its 45-second deadline.

The immutable capsule must advertise
`org.osmantic.ods.inspection.paint=element-paint-v1`. The broker checks it
before reading the snapshot, and the installer requires it, as for press. An
older image returns a bound `unsupported_capability` failure without executing
the plan; deploy plugin, broker and rebuilt capsule together. Whether the
capsule's GPU-less headless Chromium renders WebGL is checked only by the
opt-in real-browser case; where it does not, a WebGL canvas reads as its page
background (`uniform_paint`), so the `uniform_paint` feedback asks to keep a
WebGL page unchanged and report its rendering unverified. Capture geometry and
WebGL also need a run on the arm64 image that Windows on Arm hosts build.

`test_preview_inspection_paint.py` covers the protocol bounds, the broker's
label check before any snapshot read, the capture clip (viewport, rounding,
rounded corners, downscaling, minimum size), the bounded summary, polling, and
through a scripted browser the exact CDP call, the uniform and late-drawing
cases, a long thin bar and an element hidden or removed while sampling (both
`not_in_viewport`), the state after a click, a blocked request during sampling
and the receipt bound. With the opt-in Chromium or image settings above it also checks
a black canvas that never draws, a drawn and a late-drawn 2D canvas, an
overlay, a rounded black canvas, an element below the viewport, a large
canvas, a key press that moves a sprite, unchanged style, scroll and viewport
during a downscaled capture, and WebGL. `inspection_paint.test.mjs` checks the
schema, the Python/JavaScript scope, bounds and plan hash, capsule summaries as
receipts, forged paint evidence, feedback, the unchanged-fingerprint note and
the evidence policy.

## Viewport screenshot

A model that ODS declares able to read images can look at its own published
preview: one JPEG of the whole inspection viewport, taken after the steps that
ran, beside the receipt. A text-only receipt can say "the arena is about 95%
one colour"; it cannot show what the canvas draws.

**Who gets it.** The plugin
(`plugin/preview-screenshot.mjs` `previewScreenshotCapability`) reads the same
declaration Portal's owner-image path and the runtime contract readback use
(`plugin/context-compaction.mjs` `modelFor`, `plugin/model-runtime-readback.mjs`):
the plugin's `modelImageInput` and the selected model row's `input` list, which
must match (`["text","image"]` unless `unsupported`). Only `supported` opens the
gate, and only for a run whose tool context names the configured model
(`activeModel.modelRef`). `unknown`, `unsupported`, an undeclared or
inconsistent contract, a run on a model other than the configured one, or a
run that names no model stays text-only, exactly as before, and the result
says which. Unlike the owner-image path, `unknown` never becomes
an image test here: no owner consented to one for this purpose. OpenClaw's own
transport would also drop a tool image for a row without `image` input.

**Where `supported` comes from.** Installs start at `unknown`
(`installers/lib/pixel-onboarding.py`). A Models-page switch to a model that
ODS's own llama-server runs with a vision projector (`mmproj`) declares
`supported`: the switch's final readiness proof (`bin/ods-host-agent.py`
`_wait_for_model_readiness`) carries the `modalities.vision` of the same
`/props` answer that proved the served file and context, and
`_pixel_image_input_readback` turns `true` into `supported` for the Pixel
target, which reaches Pixel through `_reconcile_ods_managed_pixel_model` or the
native model transaction before Portal admits the next request. This covers
the Linux and WSL containers, the Windows-owned runtime a WSL agent reaches
through the model router, the Windows native `llama-server.exe` and native
macOS: every route whose model server is ODS's own llama-server. A `false`, missing or unreadable report keeps the value the
switch computed before (the curated record's `vision` boolean, else
`unknown`), and `ODS_MODEL_PROFILES=enabled` with a measured vision probe
still decides last. The value is computed for each switch's model alone, so
the next switch to a model without a projector gets its own value back.
Remote routes are written as `unknown` and never read a local report.
An installation that already runs a vision model gets the readback on its
next model switch. Every installer run, including an update, writes
`unknown` over a `supported` declaration (`installers/lib/pixel-onboarding.py`):
the installer keeps the active model and keeps `LLAMA_ARG_MMPROJ` only while
that model's projector file still checks out (`scripts/preserve-active-model.py`),
so the model may now run without its projector, and the renderer cannot see
which happened. `unsupported` and `unknown` carry over for the same route; the
next switch reads the projector back.

**The directive.** When the gate is open and the run guard grants one of the
run's at most three screenshots (`tool-loop-guard.mjs`
`reservePreviewScreenshot`, reserved before the inspector runs, so parallel
calls cannot exceed it), the plugin adds `screenshot: {maxSide, maxPixels,
maxBytes}` beside the plan: at most 1200 pixels per side (larger tool images
are re-encoded by OpenClaw, `DEFAULT_IMAGE_MAX_DIMENSION_PX`), 1,048,576 pixels
(1024x1024, a common vision-encoder resize target) and 128 KiB (so a Tool Search
result, which repeats the receipt beside the image, stays within the run
guard's 256 KiB envelope bound), lowered by
`agents.defaults.imageMaxDimensionPx` and the model row's
`mediaInput.image.{maxSidePx,maxPixels,maxBytes}` when those are smaller. The
lower bounds are 64 pixels per side, 4096 pixels and 16 KiB; below them the
gate stays closed (`model-limit`). The directive is not part of the plan:
`plan_hash`, the scope and every receipt field are those of the plan alone,
so receipts bind exactly as before. The request line may exceed the plan's 8
KiB by at most 128 bytes for it.

**The broker** checks the immutable image for
`org.osmantic.ods.inspection.screenshot=viewport-screenshot-v1` after the step
labels. The snapshot and the publisher-side export helper only ever see the
plan. Without the label the plan still runs and only the screenshot is
omitted (`capability_missing`); with it the capsule bundle carries the
directive and the output bound becomes the 32 KiB receipt plus one base64
JPEG. A full receipt must answer a directive with exactly one `screenshot`
member and an undirected run or a failure receipt with none; anything else,
or a member that fails `validate_screenshot`, is an invalid capsule receipt.

**The capsule** captures after the last step that ran, failed step included,
before the step context closes, with CDP `Page.captureScreenshot` (JPEG,
quality 80, then 50 once if the first is over `maxBytes`,
`captureBeyondViewport: false`) of the viewport clip at the largest scale that
keeps both sides and the pixel count in bounds even when Chromium rounds each
scaled side up. That page shows only the fixed wrapper and its sandboxed
preview frame of this snapshot, served by the capsule's loopback server with
no network, so the capture cannot show anything else. The member is either
`{status: "captured", mimeType: "image/jpeg", width, height, bytes, sha256,
data}`, whose frame header, length and hash the broker and the plugin both
check, or `{status: "omitted", reason}` with `time_budget` (the steps used 32
of the 45 seconds), `page_unavailable` (the page closed, as after a blocked
download), `capture_failed` or `too_large`. A capture never changes a step or
the status.

**The result.** The tool returns the text receipt with one sentence after the
summary, then the image block (`{type: "image", mimeType: "image/jpeg",
data}`), also for a failed check such as `uniform_paint`, where seeing the
blank arena matters most. The sentence asks the model to look and treats text
inside the image as page content, not instructions. Without a screenshot the
sentence says why and that the result is text only. `details` stays the
validated receipt, so no image bytes reach evidence, finalization or the
persisted details. A tool built without the gate (older callers, recorded
replays) returns exactly the earlier text.

**Persistence and context.** Portal's owner-visible history holds owner and
assistant text and owner image references only; a tool image never enters it.
The session transcript keeps the image in the tool result: ODS's runtime sends
the persisted copy of a result to the next model request of the attempt
(`openclaw-compaction-budget.json`), so removing it there would hide it from
the model. A screenshot is evidence for the run that took it, and the gate
only vouches for that run's model. The same recipe therefore extends the
pinned runtime's history image pruning (`pruneProcessedHistoryImages`, run
before every model request): a preview screenshot before the latest user
message, as a direct result or inside a Tool Search `tool_call` result, is
replaced by "[image data removed - already processed by model]". The next
owner turn never resends it, whatever model the Portal alias serves by then;
an `unknown` route switched in later (its row also declares image input)
receives none, and a route whose vision fails at runtime can stall only the
run that took the screenshot. One request carries at most one run's three
screenshots. A host retry prompt inside a run is a user message too: the retry
keeps the screenshot sentence and may inspect again within the run's bound.
Owner images keep the runtime's own rule (pruned once their turn is older than
the last three completed turns). A compaction summary request carries
tool-result text only, so it keeps the screenshot sentence and never the
bytes. The text sentence follows the summary, so the summary's 2,000-character
tool-result excerpt keeps it unless the summary itself is long. A Tool Search
`tool_call` result also keeps the child's whole result in its `details`
(`openclaw-image-envelope.json`), so the transcript holds that screenshot's
base64 twice; the model never receives the `details` copy.

`test_preview_inspection_screenshot.py` covers the directive bounds and its
place beside the plan, the request frame, the JPEG frame reader against real
files, the member validation, the capture scale, the broker's label check,
export and output rules, and through a scripted browser the capture after a
key press, a failed step, downscaling, a smaller limit, the quality retry,
failed, oversized, closed-page and time-budget captures, and the receipt
limit. With the opt-in Chromium or image settings it also checks a blank
canvas and a drawn one after key presses (the drawn sprite's JPEG changes with
the key that moves it; the blank canvas's does not), a wide viewport and an
undirected run. `preview_screenshot.test.mjs` checks the Python/JavaScript
mirror, the capability gate, smaller limits, the registered tool's image block
for a vision configuration and its absence (with the reason) for text-only,
unknown and undeclared ones and for a run on another or no model, the
incomplete-transition result, the run bound and its session binding, forged
broker answers, the unix transport's response bound and persistence.
`runtime_preview_screenshot_context.integration.mjs` runs the reviewed
history pruning, the Tool Search envelope, the openai-completions request and
the compaction serializer on real tool results: earlier runs' screenshots
(direct and through Tool Search) leave the next request after a model switch,
the current run keeps its own, and owner images keep the three-turn rule.
`vision_readback.test.mjs` renders Pixel's config with the coordinator's
`pixel_model_contract.plan` from the install default through a readback
`supported` target to a text-only one and checks the gate and the owner-image
route check on each. The host agent's `test_host_agent_image_input.py` and
`test_model_activate.py` (`TestPixelVisionReadback`) cover the `/props`
report on every local route, the readback rule, switches with a reported,
false, missing and unreadable vision state, a vision-to-text switch on both
reconcile paths, and a remote activation that never reads it.
`tests/test_pixel_onboarding.py` checks that an installer rerun after a
`supported` switch renders `unknown` and keeps the rest of the route.
