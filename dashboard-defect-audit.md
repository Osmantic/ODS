# Dashboard production defect audit

Audit continuation start date: `2026-09-23`

Audit target: `public-beta` dashboard frontend. Findings are admitted only when
the affected journey and source path agree and a focused live GitHub issue/PR
search finds no overlap.

## Confirmed findings

### 2. A hung status request permanently blocks dashboard polling

- Severity: P1 reliability
- Journey: open Dashboard when `/api/status` accepts the connection but never
  completes its response.
- Expected: a bounded deadline, visible timeout, and a retryable next poll.
- Actual: `useSystemStatus` has no `AbortController` or request deadline. Its
  `fetchInFlight` guard stays true until the unresolved request settles, so
  every five-second poll and visibility-triggered refresh is skipped forever.
  Initial loading also never completes.
- Evidence: `ods/extensions/services/dashboard/src/hooks/useSystemStatus.js:75-94`.
  The neighboring `useGPUDetailed` hook demonstrates the intended bounded
  15-second abort/recovery contract.
- Duplicate gate: live open issue/PR searches for
  `useSystemStatus abort timeout stuck fetch dashboard` returned no matches on
  2026-09-21.
- Regression coverage needed: hold the first response open past the deadline,
  advance timers, assert a timeout state, then let the next poll succeed.

### 3. Poll failures leave stale healthy status displayed as current

- Severity: P1 operational correctness
- Journey: load a healthy dashboard, then stop or disconnect dashboard-api.
- Expected: the UI marks the last snapshot stale or unavailable.
- Actual: the catch path only records `error`; it preserves the prior `status`.
  Because `App` discards `error`, prior GPU, model, uptime, and service-health
  values continue to look live while every refresh is failing.
- Evidence: `ods/extensions/services/dashboard/src/hooks/useSystemStatus.js:86-88`
  and `ods/extensions/services/dashboard/src/App.jsx:65`.
- Duplicate gate: live open issue/PR searches for
  `dashboard stale healthy status API failure telemetry` returned no matches on
  2026-09-21.
- Regression coverage needed: return a healthy snapshot, reject the next poll,
  and assert that every retained metric is visibly marked stale.

### 4. First-run devices render the normal workspace before setup status resolves

- Severity: P1 onboarding / access-boundary correctness
- Journey: open a genuinely unconfigured device while `/api/setup/status` is
  delayed.
- Expected: retain a neutral loading gate until the authoritative first-run
  status is known, then render only the setup wizard.
- Actual: `useFirstRun` initializes `firstRun=false, loading=true`, but `App`
  discards `loading` and immediately renders the normal workspace. The wizard
  replaces it only after the response arrives, contradicting the documented
  lockout and exposing routes during the delay.
- Evidence: `src/hooks/useFirstRun.js:23-25`, `src/App.jsx:71`, and
  `src/App.jsx:108`.
- Duplicate gate: live open issue/PR searches for
  `first run setup loading wizard flash dashboard` returned no matches on
  2026-09-21.
- Regression coverage needed: hold setup status pending and assert that no
  workspace navigation or agent composer is rendered before the response.

### 5. A hung setup-status request can suppress onboarding forever

- Severity: P1 onboarding availability
- Journey: boot an unconfigured device when `/api/setup/status` accepts the
  request but never completes.
- Expected: a bounded timeout with a retryable setup-status error.
- Actual: the hook has no abort deadline. Because `firstRun` defaults false and
  `App` ignores both `loading` and `error`, the normal workspace remains visible
  indefinitely and the required wizard never appears.
- Evidence: `src/hooks/useFirstRun.js:27-45` and `src/App.jsx:71`.
- Duplicate gate: live open issue/PR searches for
  `setup status request timeout first run` returned no matches on 2026-09-21.
- Regression coverage needed: leave the endpoint unresolved, advance the
  deadline, assert a blocking retry surface, then recover with `first_run=true`.

### 6. Overlapping setup-status refreshes can apply responses out of order

- Severity: P1 state correctness
- Journey: initial status request is slow; setup completes and calls
  `refreshFirstRun`; the newer request returns `false`, then the older request
  returns `true`.
- Expected: only the newest refresh may commit state.
- Actual: `refresh` has no generation token, single-flight guard, or abort of
  the previous request. Any older response can overwrite the newer result and
  re-open the wizard after successful setup (or hide it in the reverse race).
- Evidence: `src/hooks/useFirstRun.js:27-45` and the completion-triggered
  refresh at `src/App.jsx:80-84`.
- Duplicate gate: live open issue/PR searches for
  `setup status stale response race wizard` returned no matches on 2026-09-21.
- Regression coverage needed: control two response promises and resolve them in
  reverse order; assert that only the second request determines final state.

### 7. Update-banner dismissal is falsely acknowledged when storage is blocked

- Severity: P2 persistent UX correctness
- Journey: dismiss an available-update banner in a browser where localStorage
  writes throw (private mode, quota, or policy), then reload.
- Expected: either persist the dismissal or tell the user it could not be saved.
- Actual: `dismissUpdate` swallows the storage exception and still updates only
  React memory. The banner disappears as if saved, but reappears after reload.
- Evidence: `src/hooks/useVersion.js:75-80`.
- Duplicate gate: live open issue/PR searches for
  `dismiss update localStorage reload banner` returned no matches on 2026-09-21.
- Regression coverage needed: make `setItem` throw, dismiss, remount the hook,
  and assert a visible persistence warning rather than false success.

### 8. A forced update check can be dropped while the background check is active

- Severity: P2 update correctness
- Journey: click “Check for updates” while `useVersion` has an in-flight
  background request.
- Expected: the explicit forced check refreshes the global update banner after
  the active request finishes.
- Actual: Settings dispatches `ods-version-checked`, but `checkVersion` returns
  immediately when `inFlight` is true and does not queue a pending refresh.
  Settings can report the forced result while the global banner remains based
  on an older/non-forced response.
- Evidence: `src/pages/Settings.jsx:207-226` and
  `src/hooks/useVersion.js:20-23,63-64`.
- Duplicate gate: live open issue/PR searches for
  `version check event in flight dropped update` returned no matches on
  2026-09-21.
- Regression coverage needed: hold the background request, dispatch the event,
  settle it, and assert that a second request is issued and committed.

### 9. Configuration export reports success for non-2xx status responses

- Severity: P2 data integrity
- Journey: export configuration before Settings has cached status while
  `/api/status` returns a JSON 401/500 response.
- Expected: reject the response and show “Export failed”.
- Actual: `handleExportConfig` calls `fetchJson(...).json()` without checking
  `response.ok`, builds a mostly undefined configuration from the error body,
  downloads it, and displays “Configuration exported.”
- Evidence: `src/pages/Settings.jsx:42-50` and `src/pages/Settings.jsx:365-386`.
- Duplicate gate: live open issue/PR searches for
  `settings export config response ok error JSON` and
  `configuration export failed HTTP status` returned no matches on 2026-09-21.
- Regression coverage needed: return a JSON 500 response and assert that no
  Blob URL/click is created and the danger notice contains the HTTP failure.

### 11. Incomplete GPU identity data crashes the whole monitor

- Severity: P1 availability
- Journey: GPU detail API returns a discovered device without `name` or `uuid`
  (partial driver/sysfs result).
- Expected: render a stable fallback identity and preserve other telemetry.
- Actual: `GPUCard` calls `gpu.name.replace(...)` and `gpu.uuid.slice(...)`
  without validation, throwing during render and taking down the route.
- Evidence: `src/components/GPUCard.jsx:37-40`.
- Duplicate gate: live open issue/PR searches for
  `GPU card missing uuid name dashboard crash` returned no matches on
  2026-09-21.
- Regression coverage needed: render the real card with each identity field
  missing and assert accessible fallback labels without an error boundary hit.

### 12. Out-of-range topology links crash matrix construction

- Severity: P1 availability
- Journey: topology API returns a stale/malformed link whose `gpu_a` or `gpu_b`
  lies outside the reported GPU count.
- Expected: ignore/reject the invalid link and show remaining topology.
- Actual: `buildMatrix` directly indexes `m[link.gpu_a][link.gpu_b]`; an
  out-of-range first index produces `undefined[...]` and throws before render.
- Evidence: `src/components/TopologyView.jsx:14-22`.
- Duplicate gate: live open issue/PR searches for
  `GPU topology invalid link index frontend crash` returned no matches on
  2026-09-21.
- Regression coverage needed: supply negative and too-large endpoints and
  assert invalid links are omitted with an explicit data warning.

### 13. Unbounded topology count can freeze or exhaust the browser

- Severity: P1 availability / resource exhaustion
- Journey: corrupted topology payload reports an implausibly large
  `gpu_count`.
- Expected: validate against a small hardware ceiling and actual GPU records.
- Actual: `buildMatrix` allocates an `n × n` nested array and the component
  renders an `n × n` table directly from untrusted response size. A large count
  can allocate millions of cells and lock the dashboard tab.
- Evidence: `src/components/TopologyView.jsx:14-18,29-30,67-99`.
- Duplicate gate: live open issue/PR searches for
  `GPU topology gpu_count unbounded matrix browser memory` returned no matches
  on 2026-09-21.
- Regression coverage needed: pass an oversized count and assert bounded,
  rejected rendering rather than matrix allocation.

### 14. Full service map hides refresh failures while labeling stale data live

- Severity: P1 operational correctness
- Journey: load the full Integrations map successfully, then make `/api/status`
  fail.
- Expected: preserve the last snapshot but visibly mark it stale/failed.
- Actual: `fetchTopology` retains nodes and sets `error`; the full rendering
  branch never displays that error and continues showing `live · 10s`. Only the
  compact Settings rendering exposes the refresh failure.
- Evidence: `src/pages/ServiceMap.jsx:350-355,389-403`; the compact-only alert
  is at `src/pages/ServiceMap.jsx:297`.
- Duplicate gate: live open issue/PR searches for
  `service map stale live poll failure error` returned no matches on 2026-09-21.
- Regression coverage needed: fail a poll after success and assert the full map
  exposes failure and capture time rather than a live badge.

### 15. Selected service details remain stale after topology refresh/removal

- Severity: P2 state correctness
- Journey: select a service, then refresh after its status/port changes or the
  service disappears.
- Expected: reconcile the selection with the new node or close the panel.
- Actual: `selectedNode` stores the old object and is never reconciled when
  `topology.nodes` changes. The details panel can keep presenting an obsolete
  status, port, and launch link indefinitely.
- Evidence: `src/pages/ServiceMap.jsx:323-329,360-362,444-445`.
- Duplicate gate: live open issue/PR searches for
  `service map selected detail stale removed refresh` returned no matches on
  2026-09-21.
- Regression coverage needed: select a node, replace/remove it in the next API
  snapshot, and assert updated details or panel closure.

### 16. Colliding service IDs corrupt map identity and dependency resolution

- Severity: P2 topology correctness
- Journey: status payload contains two records sharing an explicit ID or names
  that normalize to the same slug.
- Expected: reject/disambiguate duplicate identities.
- Actual: `buildTopology` preserves both nodes while `nodeById` and `positions`
  overwrite earlier entries; React also receives duplicate keys. Edges and
  selection can target one record while rendering another.
- Evidence: `src/pages/ServiceMap.jsx:128-158,162-177,439`.
- Duplicate gate: live open issue/PR searches for
  `service map duplicate service id collision` returned no matches on
  2026-09-21.
- Regression coverage needed: provide duplicate explicit and slug-derived IDs
  and assert deterministic rejection or unique canonicalization.

### 17. Remote peer download progress never refreshes automatically

- Severity: P1 long-running operation observability
- Journey: start downloading a model to a remote ODS peer and leave the page
  open.
- Expected: status/progress advances until completion or failure.
- Actual: the component fetches peer download status only on initial status
  load, manual refresh, and immediately after an action. There is no polling or
  visibility-resume effect, so an active download can remain frozen at its
  initial receipt indefinitely and completion is never surfaced automatically.
- Evidence: `src/pages/RemoteProvider.jsx:367-399,462-492`; there is no
  `setInterval` in the component.
- Duplicate gate: live open issue/PR searches for
  `remote peer download status polling progress dashboard` returned no matches
  on 2026-09-21.
- Regression coverage needed: start a download, advance timers through status
  changes, and assert automatic completion/failure rendering.

### 18. Remote-provider refresh responses can overwrite newer state

- Severity: P1 state correctness
- Journey: start two status refreshes; the newer response contains reconciled
  route state and returns first, then the older pre-change response returns.
- Expected: only the newest request commits.
- Actual: `loadStatus` has no request generation or active-controller guard,
  and the Refresh button remains available. Any late older response can replace
  newer `statusData` and repopulate the form with stale route values.
- Evidence: `src/pages/RemoteProvider.jsx:342-365,548-556`.
- Duplicate gate: live open issue/PR searches for
  `remote provider status stale response refresh race` returned no matches on
  2026-09-21.
- Regression coverage needed: resolve two status promises in reverse order and
  assert the newer snapshot remains authoritative.

### 19. Route probe and lifecycle mutation can run concurrently

- Severity: P1 configuration integrity
- Journey: start a long route probe, then configure/disable/remove the route;
  or start a lifecycle mutation and click Test route.
- Expected: serialize operations that both prove/commit route state.
- Actual: the Test button is disabled only by `testing`, while lifecycle
  buttons use a separate `lifecycleBusy` flag. Both operations can overlap for
  up to 30 minutes and commit receipts/state against different configurations.
- Evidence: `src/pages/RemoteProvider.jsx:401-460,526-530,565-575`.
- Duplicate gate: live open issue/PR searches for
  `remote provider probe configure concurrently race` returned no matches on
  2026-09-21.
- Regression coverage needed: hold one operation open and assert every
  conflicting action is disabled and cannot issue a second mutation.

### 20. A displayed remote-provider plan remains valid-looking after form edits

- Severity: P2 operator safety
- Journey: generate a configuration plan, then change endpoint/model/token
  values before clicking Configure.
- Expected: invalidate the old plan or require replanning for the new payload.
- Actual: `updateForm` marks the form dirty but does not clear `planResult`.
  Configure submits the current form, while the visible plan still describes
  the previous payload, so operator review no longer matches the mutation.
- Evidence: `src/pages/RemoteProvider.jsx:402-414,400,790-801`.
- Duplicate gate: live open issue/PR searches for
  `remote provider plan stale after form edit` returned no matches on
  2026-09-21.
- Regression coverage needed: plan payload A, edit to payload B, and assert the
  plan disappears and Configure remains gated until B is replanned.

### 22. Owner-card readiness can keep Finish disabled forever

- Severity: P1 onboarding availability
- Journey: readiness endpoint accepts the request but never completes.
- Expected: bounded timeout and a recoverable unavailable/retry state.
- Actual: the mount request has no abort deadline. `ownerCardStatus` remains
  null, `ownerCardStatusLoading` stays true, and Finish remains disabled for the
  entire session.
- Evidence: `src/pages/FirstBoot.jsx:93-123,578-613`.
- Duplicate gate: live open issue/PR searches for
  `owner card status request timeout finish disabled wizard` returned no
  matches on 2026-09-21.
- Regression coverage needed: leave readiness pending, advance the deadline,
  and assert that setup can retry or proceed through the documented fallback.

### 23. First-boot mutations can leave “Configuring…” stuck indefinitely

- Severity: P1 onboarding availability
- Journey: template apply, owner-card generation, or setup completion accepts a
  connection but never returns.
- Expected: operation-specific deadlines and an ambiguity-safe recovery receipt.
- Actual: all finish mutations use bare `fetch` calls without signals or
  deadlines. `finishing` remains true, Back and Finish stay disabled, and the
  only recovery is abandoning/reloading the wizard without knowing what applied.
- Evidence: `src/pages/FirstBoot.jsx:134-221,599-613`.
- Duplicate gate: live open issue/PR searches for
  `first boot finish request timeout configuring forever` returned no matches
  on 2026-09-21.
- Regression coverage needed: stall each mutation boundary and assert a bounded
  uncertain-result state that prevents blind replay.

### 24. Retrying Finish re-applies an already successful stack template

- Severity: P1 mutation safety
- Journey: stack apply succeeds, then owner-card generation or setup completion
  fails; user retries Finish.
- Expected: persist/reconcile the successful phase and resume from the failed
  step.
- Actual: `finish` always starts again at template apply. No phase receipt is
  retained, so retries replay a potentially expensive/non-idempotent stack
  mutation before retrying the later operation.
- Evidence: `src/pages/FirstBoot.jsx:126-178`; catch retains no completed-phase
  state at `src/pages/FirstBoot.jsx:253-257`.
- Duplicate gate: live open issue/PR searches for
  `first boot finish retry apply template twice` returned no matches on
  2026-09-21.
- Regression coverage needed: succeed apply, fail generate, retry, and assert
  apply is called exactly once.

### 27. Setup progress is invisible to assistive technology

- Severity: P2 accessibility
- Journey: complete the four-step first-boot wizard with a screen reader.
- Expected: a named progress indicator announcing current step and total.
- Actual: StepDots renders four unlabelled decorative `div` elements with only
  color/ring state. There is no progressbar/list semantics, accessible name,
  current marker, or text alternative.
- Evidence: `src/pages/FirstBoot.jsx:326-342`.
- Duplicate gate: live open issue/PR searches for
  `setup wizard progress indicator accessibility step` returned no matches on
  2026-09-21.
- Regression coverage needed: assert an accessible progress indicator reports
  “Step N of 4” as navigation changes.

### 28. The owner-card URL field has no accessible name

- Severity: P2 accessibility / credential usability
- Journey: reach the owner-card success screen and locate the manual URL with
  a screen reader or voice-control tool.
- Expected: a labelled read-only field such as “Owner card link”.
- Actual: the input has a value and focus behavior but no `<label>`,
  `aria-label`, or `aria-labelledby`; its purpose is unavailable to assistive
  technology independent of the adjacent copy button.
- Evidence: `src/pages/FirstBoot.jsx:687-705`.
- Duplicate gate: live open issue/PR searches for
  `owner card link input accessible label first boot` returned no matches on
  2026-09-21.
- Regression coverage needed: query the success screen by textbox role and
  accessible name and assert it exposes the owner URL.

### 29. Remote-provider asynchronous failures are not announced

- Severity: P2 accessibility / operator safety
- Journey: trigger status, probe, lifecycle, or peer-model failure while using
  a screen reader.
- Expected: dynamic failures use `role="alert"` or an assertive live region.
- Actual: the error containers are plain `div` elements. They appear visually
  after asynchronous actions but do not reliably announce, leaving a nonvisual
  operator without the reason an operation stopped.
- Evidence: `src/pages/RemoteProvider.jsx:577-610,684-691`.
- Duplicate gate: live open issue/PR searches for
  `remote provider async error aria live alert` returned no matches on
  2026-09-21.
- Regression coverage needed: trigger each async error and assert its message
  appears in an alert live region.

### 30. Non-fatal admin-session mint can block completed setup forever

- Severity: P1 post-commit availability
- Journey: template/card/setup completion all succeed, then
  `/api/auth/admin-session` accepts the request but never responds.
- Expected: because session minting is documented as non-fatal, transition to
  the owner-card success screen without waiting indefinitely.
- Actual: the request is awaited without a deadline before `clearProgress` and
  `setInvite`/`onComplete`. A hung optional request leaves “Configuring...” on
  screen even though server-side setup is already committed.
- Evidence: `src/pages/FirstBoot.jsx:223-252`.
- Duplicate gate: live open issue/PR searches for
  `admin session hangs setup complete configuring first boot` returned no
  matches on 2026-09-21.
- Regression coverage needed: leave admin-session pending after successful
  completion and assert the success screen appears within a bounded interval.

## Audit constraints

- The local browser journey is functional after restoring disposable
  `node_modules` dependencies without changing manifests or lockfiles.
- Candidate findings are not promoted into this document until live duplicate
  checks and behavioral/source evidence both pass.

## Remaining findings (63 total; original numbers retained for traceability)

The following additional independent findings were source-confirmed on this
local `public-beta` checkout. Focused searches for each symptom in current
Osmantic/ODS PRs and issues found no matching report; malformed-payload cases
still require the listed regression test for runtime confirmation.

Removed after the live recheck: #1 (existing issue #1994), #10 (existing issue
#3242), #21 (PR #5529), #25 (PR #5527), #26 (existing issue #1387), #31 and
#33 (existing storage/history coverage), #47 (PR #5612), #49 (PR #5655),
#58–59 (PR #5753), #71–73 (PRs/issues #5529, #5527, and #3243), #41
(current page is clamped), #50 (single-flight polling plus late-result coverage),
and #77 (identity save is single-flight).

32. **Service CPU cache has no total key/sample budget (P2).**
`Dashboard.jsx:338-348` hydrates every accepted object entry and sample from
local storage. A large browser-written cache creates a synchronous render
payload. Gate: `dashboard service cpu history unbounded cache`.

34. **Service rename silently resets CPU history (P2).**
History is keyed directly by mutable `service.id` at `Dashboard.jsx:417-433`;
renames lose continuity and strand old keys. Gate: `dashboard service rename history`.

35. **Feature metadata failure remains “loading” (P2).**
`Dashboard.jsx:619-640,858` fetches `/api/features` but does not expose a
retryable error state. A 500 leaves misleading loading copy. Gate:
`dashboard feature metadata loading retry`.

36. **Resource polling has no request deadline (P2).**
`Dashboard.jsx:646-663` fetches resources without abort/timeout; a hung request
can block useful updates and overlap later polls. Gate: `dashboard resources fetch timeout`.

37. **Resource polls can commit out of order (P2).**
The same path writes every response without a generation check. A slow older
response can replace a newer resource snapshot. Gate: `dashboard resources stale response race`.

38. **Restart completion can update after route change (P2).**
`Dashboard.jsx:1407-1421` schedules delayed state work without cancellation or
service-generation checking. Gate: `dashboard restart timeout unmount`.

39. **Restart errors can hide useful non-JSON server details (P2).**
`Dashboard.jsx:1409-1411` assumes JSON before preserving a failing response;
text/plain 502 responses collapse to a generic parse error. Gate:
`dashboard restart non-json error`.

40. **Missing service ids collide in action state (P2).**
`Dashboard.jsx:1407-1424,1475` indexes action state by `service.id`; two
id-less entries share loading/error state. Gate: `dashboard duplicate service id action`.

42. **Uncontrolled compact `<details>` rows can retain another service’s open state (P2).**
Rows are keyed by mutable id/name/index in `CompactDashboard.jsx:61`; refresh
and reorder can reuse native disclosure state for the wrong row. Gate:
`compact dashboard details stale row`.

43. **Full Models route has no search control (P2).**
`Models.jsx:387` renders search only when `compact` is true, making a large full
catalog needlessly undiscoverable. Gate: `dashboard models full search missing`.

44. **Model source tabs lack tabpanel relationships (P2 a11y).**
`Models.jsx:554-591` sets `role=tab` and `aria-selected` but no associated
`aria-controls`/`tabpanel`. Gate: `dashboard model tabs aria-controls`.

45. **Concurrent model activations can finish in the wrong order (P1).**
`useModels.js:218-253` commits activation results without a request generation;
selecting B then receiving A can make A appear active. Gate:
`dashboard model activation stale response`.

46. **Delayed model polling can survive route unmount (P2).**
`useModels.js:353-434` clears its interval but scheduled delayed retries are not
all cancelled by the owning view. Gate: `dashboard models polling after unmount`.

48. **Model delete confirmation can target a replaced catalog entry (P2).**
`Models.jsx:1044-1066` retains the original object while refresh can replace
the catalog. Gate: `dashboard model delete stale confirmation`.

51. **Usage action text errors can be lost on invalid JSON (P2).**
`Usage.jsx:251` assumes a JSON payload for a failed response. Gate:
`dashboard usage action non-json error`.

52. **Invite list refresh has no stale-response guard (P1).**
`Invites.jsx:105-125` allows initial load and manual refresh to commit in either
order, potentially restoring a revoked link visually. Gate: `dashboard invites stale refresh`.

53. **Concurrent invite revokes can rebuild stale list state (P2).**
`Invites.jsx:136-157` has no request-level generation/idempotency barrier for
two simultaneous DELETEs. Gate: `dashboard invite revoke concurrent`.

54. **Invite clipboard denial has no reliable failure path (P2).**
`Invites.jsx:652-669` assumes `navigator.clipboard.writeText` succeeds; users
can receive no actionable fallback. Gate: `dashboard invite clipboard denial`.

55. **QR generation can remain pending without a modal retry state (P2).**
`Invites.jsx:645-660` starts QR fetch without an explicit bounded retry UI.
Gate: `dashboard invite qr hangs`.

56. **Settings export downloads 500 JSON as configuration (P1).**
`Settings.jsx:354-369` calls `.json()` before checking `response.ok`, so an API
error body becomes a downloadable “export”. Gate: `dashboard settings export non-ok`.

57. **Settings export can write internal diagnostics to a user file (P2).**
The same path serializes the non-OK body, leaking server error fields into the
download. Gate: `dashboard settings export error payload`.

60. **ServiceMap Space activation can scroll the page (P2 a11y).**
`ServiceMap.jsx:205-210` handles keyboard activation but does not prevent the
Space default action. Gate: `dashboard servicemap space scroll`.

61. **Nameless topology nodes can abort filtering/rendering (P2).**
`ServiceMap.jsx:297-304` assumes name-bearing node payloads during list/search
rendering. Gate: `dashboard servicemap missing node name`.

62. **Manual ServiceMap refresh overlaps the fixed poll (P2).**
`ServiceMap.jsx:345-368` has no single-flight/abort guard. Gate:
`dashboard servicemap refresh overlap`.

63. **Map view can retain stale topology without an error indicator (P2).**
`ServiceMap.jsx:293-304,345-368` exposes failure handling on the list path but
does not clearly mark the SVG map stale. Gate: `dashboard servicemap map stale error`.

64. **Extension progress polling survives modal close (P2).**
`Extensions.jsx:1075-1155` combines intervals and recursive timeouts without a
common cancellation token. Gate: `dashboard extension modal polling close`.

65. **Older extension progress can overwrite a newer failure (P2).**
`Extensions.jsx:200-208,1075-1105` commits every response without generation
checking. Gate: `dashboard extension progress stale poll`.

66. **Extension confirmation does not restore trigger focus (P2 a11y).**
`Extensions.jsx:536-550` auto-focuses Cancel but has no focus-return mechanism.
Gate: `dashboard extension confirmation focus return`.

67. **Extension Details dialog does not trap focus (P2 a11y).**
`Extensions.jsx:930-954` declares `aria-modal` without focus containment. Gate:
`dashboard extension details focus trap`.

68. **Extension toast close button has no accessible name (P2 a11y).**
`Extensions.jsx:578-584` renders only `×`. Gate: `dashboard extension toast close name`.

69. **Extension command clipboard rejection has no feedback (P2).**
`Extensions.jsx:1301-1302` chains only the success branch. Gate:
`dashboard extension command clipboard rejection`.

70. **Extension logs can continue polling after terminal status (P2).**
`Extensions.jsx:1124-1155` schedules recursive log fetches without consistently
stopping on terminal install state. Gate: `dashboard extension logs terminal poll`.

74. **Template Apply has a keyboard double-submit window (P1).**
`TemplatePicker.jsx:158-176` relies on asynchronous disabled-state updates;
two same-turn activations can issue duplicate POSTs. Gate:
`dashboard template apply double submit`.

75. **Settings route filtering has no result announcement (P2 a11y).**
`Settings.jsx:629-640` changes the result set without a live result summary.
Gate: `dashboard settings route filter screenreader`.

76. **Host-disk progress can expose invalid ARIA values (P2 a11y).**
`Settings.jsx:726` uses raw backend disk percentage without finite/range
normalization. Gate: `dashboard settings disk invalid aria`.

78. **Pending identity load can overwrite a completed save (P1).**
The same context lets the initial GET commit after a POST. Gate:
`dashboard portal identity load overwrites save`.

79. **Aborted identity save is surfaced as a real failure (P2).**
`PortalIdentityContext.jsx:30-64` routes controller abort through generic error
state, producing a false failure after navigation/unmount. Gate:
`dashboard portal identity abort error`.

80. **Portal API error text is unbounded and trusted as UI copy (P1).**
`src/components/PortalResponseError.jsx` and callers render server error text
without a common length/trust normalization, permitting misleading or enormous
error surfaces. Gate: `dashboard portal response error untrusted text`.

## Continuation work started 2026-09-23

### 81. Compact dashboard resize callbacks can crash after unmount

- Severity: P1 reliability
- Journey: open the compact dashboard, then navigate away while a resize notification is queued.
- Expected: late observer notifications are ignored after unmount.
- Actual: the callback dereferences `list.current` after cleanup and can throw `TypeError: Cannot read properties of null (reading 'getBoundingClientRect')`.
- Evidence: `ods/extensions/services/dashboard/src/components/CompactDashboard.jsx:31-43`; live console testing on `http://127.0.0.1:3001/extensions` recorded the error.
- Duplicate gate: live issue/PR searches for `CompactDashboard ResizeObserver` returned no matching defect; existing compact-row PRs address row identity, not observer lifecycle.
- Regression coverage: invoke a queued resize callback after unmount and assert that it does not throw.

### 82. Feature enable failures leave an opened dialog blank

- Severity: P2 usability
- Journey: open an available feature from Feature Discovery while the
  instructions endpoint returns a failure or invalid response.
- Expected: the dialog remains visible with an actionable error and retry.
- Actual: `EnableInstructions` previously converted every non-OK response to
  `null` and returned no dialog content, so the click appeared to do nothing.
- Evidence: `ods/extensions/services/dashboard/src/components/FeatureDiscovery.jsx:249-302`.
- Duplicate gate: live issue/PR searches for dashboard feature-enable retry/error
  behavior returned no matching defect; broader feature-readiness work was not
  this dialog failure path.
- Regression coverage: force a 503, assert a visible alert and Retry control,
  then resolve the retry and assert the instructions render.
