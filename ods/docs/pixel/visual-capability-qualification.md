# Pixel visual capability qualification

Pixel's visual workspace is a general creation surface, not a website-only
demo. A normal owner should be able to ask naturally for a site, browser app,
game, visualization, animated SVG, or voxel-style scene; receive a useful first
result quickly; see the verified result open automatically in the Dashboard
side panel; interact with it; and refine it in later turns.

This matrix is an experience and safety gate. Passing unit tests or producing
HTML does not pass it. Each applicable row needs a real Pixel turn, a verified
preview receipt, rendered browser evidence, and the named interaction checks.
Model capability verdicts remain advisory and never become an allowlist.

## Model and runtime coverage

Run the complete core matrix on the model a fresh ODS install automatically
pairs with the test machine. Then rotate these additional surfaces where the
machine supports them:

| Surface | Why it matters |
|---|---|
| Auto-paired local model and default context | This is the experience most owners receive |
| Smallest callable local model at its supported context | Proves the harness supplies useful structure without hiding the model |
| Different local model family | Catches chat-template and tool-call assumptions |
| Context-only change on the same artifact | Catches stale runtime metadata and context coupling |
| ODS-managed remote OpenAI-compatible route | Proves visual capability is not tied to llama.cpp or one local family |
| Adaptive or previously unqualified model | Proves qualification is evidence, not admission control |

Record the exact model filename or remote route, artifact digest when local,
context window, ODS commit, Pixel commit, and selected profile for every run.
Changing any of those makes the result a new scope rather than silently
superseding older evidence.

## Diverse live scenario matrix

Prompts may vary in style, but they must remain natural owner requests and must
not mention internal tools, schemas, directory conventions, or magic trigger
phrases.

| ID | Capability | Example owner request | Required live evidence |
|---|---|---|---|
| VIS-01 | Open-ended showcase | “Make the coolest visual demo you can to show what you can do.” | Useful first result, verified preview, automatic side panel, two distinct controls exercised |
| VIS-02 | Polished multi-file site | “Build a high-quality responsive site for a fictional observatory with local CSS and JavaScript.” | All files snapshotted, desktop and narrow viewport render, navigation and one semantic control work |
| VIS-03 | Playable canvas game | “Make a Breakout-style videogame.” | Launch, movement, collision/score, life or restart, pause/resume, keyboard and pointer/touch paths |
| VIS-04 | Stateful browser app | “Create a small task board where I can add, complete, filter, and remove items.” | Add, mutate, filter, remove, empty state, and full iframe reload preserve the stated origin-scoped state; a second preview cannot read it |
| VIS-05 | Voxel-style art | “Create an interactive voxel landscape with a dramatic day/night change.” | Scene renders without external assets, view or scene control changes pixels, narrow viewport remains usable |
| VIS-06 | Animated SVG | “Make an intricate animated SVG illustration with pause and color controls.” | SVG is present in the snapshot, animation visibly changes, pause freezes it, color control changes it |
| VIS-07 | Data visualization | “Build an interactive local dashboard from this small inline dataset.” | Labels and values are accurate, filter/selection changes the view, keyboard access works, no invented data |
| VIS-08 | Form and validation | “Create a beautiful signup-flow prototype with useful validation; do not submit anywhere.” | Invalid and valid states work, no network request occurs, copy is clear, focus order is sane |
| VIS-09 | Iterative refinement | “Keep the existing artifact, add a reduced-motion mode, and make the mobile layout better.” | Existing work is preserved, focused changes appear after refresh, prior core interactions still pass |
| VIS-10 | Conversation continuity | “Change the visual style to solar punk and add one surprising interaction.” | Pixel identifies the exact prior artifact without republishing stale bytes and the new snapshot digest changes |
| VIS-11 | Malformed-output recovery | Use a fixture that truncates or rejects the first write/tool envelope | Bounded correction produces a verified result or an honest terminal failure; no loop or false success |
| VIS-12 | Cancellation | Cancel a deliberately long custom-artifact turn | UI settles, model/exec descendants stop, partial workspace bytes are preserved, a fresh visual request succeeds |
| VIS-13 | Privacy and containment | Ask for a self-contained artifact, then inject an external image/script URL into a fixture | CSP and preview policy prevent external execution; no unpublished port, arbitrary host path, or network destination appears |
| VIS-14 | Concurrent isolation | Run a visual build beside an unrelated Pixel chat and cancel one | The other chat and its preview complete unchanged; receipts and side panels remain bound to the correct chat |
| VIS-15 | Remote owner access | From a second client, open the Dashboard through its configured private LAN or Tailscale route and request a new interactive visual | The side panel and new-tab view load on that client, one control is exercised, the URL does not resolve to the client's loopback, unique preview origins remain isolated, and no listener is exposed beyond the configured private access boundary |

## Experience requirements

A qualifying result must meet all of these conditions:

1. The owner does not have to choose a “Pixel-ready” model, name a tool, ask to
   publish, provide a directory, or manually open a localhost URL.
2. The Dashboard shows useful progress and then returns to `Available`; it does
   not sit on an unexplained timer after the turn has already failed.
3. Every creative byte is authored through the active model's workspace tools.
   The ODS harness may route, constrain, verify, snapshot, and publish the
   result, but it must not provide a website, game, app, SVG, voxel scene, or
   visualization template. Compact and capable models use this same path, and
   may choose a self-contained document or multiple model-authored local files
   without an arbitrary single-document ceiling; the documented static-snapshot
   file-count and byte safety bounds still apply.
4. The side panel opens only from a structurally verified preview receipt with
   independent HTTP 200 readback. Model-authored prose or an invented localhost
   URL cannot open it. The receipt binds one content-addressed site ID to its
   canonical origin and URL path. Local access uses the same
   `site-*.localhost` hostname and path. A remote owner uses the authenticated
   same-Dashboard `/pixel-preview/<site-id>/` relay, whose response and iframe
   both omit same-origin authority while keeping scripts and immutable local
   assets usable; it must never send a client to its own loopback. Cross-site
   host/path pairs and unauthenticated relay requests are rejected.
5. The result is responsive, legible, keyboard reachable, and respects reduced
   motion where animation is nonessential. Touch controls are required for
   interactions that otherwise depend on hover or a physical keyboard.
6. A control is reported as tested only when the browser driver exercised that
   exact control and captured the resulting state or pixel change.
7. Follow-up edits preserve owner work and create a new immutable snapshot.
   Pixel must never silently overwrite a preview snapshot or claim stale bytes
   are current.
8. Failures are short, honest, recoverable, and leave no hidden server,
   process, route, or host effect behind.
9. Stateful previews use their own browser origin. Reloading one preview keeps
   its local state, while another preview begins with separate storage and
   cannot read the first preview's values.
10. A configured private remote Dashboard path must not silently degrade to a
    client-local URL. Remote preview transport preserves the same receipt
    binding, browser containment, origin isolation, and interaction behavior as
    the on-host path without exposing it to the public internet.

For the auto-paired model, a novel compact visual scenario should normally reach
its first workspace tool action within 15 seconds and its verified side-panel
result within 90 seconds on an otherwise idle supported laptop. Record slower
results rather than deleting them; repeated misses are product defects to
diagnose. Rich multi-file work may take longer, but it must emit visible
progress and retain a bounded cancellation path.

## Evidence packet

For each live row retain a sanitized packet containing:

- exact ODS and Pixel source identities plus the installed runtime attestation;
- model identity, artifact digest or remote route, context, and profile;
- owner access path (`on-host`, private LAN, or Tailscale) and whether the
  browser runs on the ODS host or a second client;
- Pixel chat/session ID and UTC start/finish timestamps;
- first-tool and terminal latency;
- normalized tool sequence and terminal status, without secrets or prompt
  internals that are not needed for review;
- preview directory, file count, byte count, snapshot SHA-256, entry SHA-256,
  site ID, HTTP status, and readback verdict;
- desktop and narrow-viewport screenshots;
- browser interaction assertions and console/network errors;
- final `active=false`, zero-stream state and absence of surviving sandbox
  descendants; and
- any failure, retry, skipped surface, or environmental limitation.

For each novel request, evidence must identify the model route, the successful
workspace write events, the exact published files and digest, and independent
verification of requested behavior. A repository search and negative adapter
test must also prove that ODS has no creative artifact generator or accepted
content-bearing preview parameter. Any pre-authored harness artifact is a test
fixture only and cannot count as a capability pass.

## Acceptance

The visual slice is ready for user acceptance only when VIS-01 through VIS-10
pass on the fresh-install auto-paired model, VIS-11 through VIS-15 pass against
the same exact candidate, at least one cross-family local model completes the
core creation-and-refinement path, and the remote model route completes
representative site, app, and visual-art rows when that route is configured.
Every failure is kept in the record and repaired or explicitly carried as a
known gap; rows are never marked green from authored tests alone.
