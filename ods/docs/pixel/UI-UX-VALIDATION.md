# ODS workspace UI/UX integration

## Review and merge order

This is a next-minor/mainline contribution, not a stable 2.6.x hotfix.

1. [ODS #3385](https://github.com/Osmantic/ODS/pull/3385): OpenClaw Pixel/Portal.
2. [ODS #3818](https://github.com/Osmantic/ODS/pull/3818): runtime settings and
   provider/access integration, based on #3385.
3. The workspace UI/UX contribution, based on #3818. Retarget it to `main` only
   after both dependencies land and recheck the resulting diff and CI.

The integration base combines #3818 `bbbb2542` and #3385 `e0a3f5f5`. The base
refresh contains two newer #3385 fixes; it does not claim their authorship.
Do not merge a dependent PR into Mike's feature branch as a substitute for this
order. Parent acceptance requirements remain in force.

## Included

- Persistent Pixel chat with resizable utility panels; consistent minimal
  Dashboard, Extensions, Models, Settings and Usage layouts.
- Height-aware library pagination, starter collections, service icons, local
  typography, current ODS favicon/logo and reduced-motion-aware mascot.
- Default Pixel appearance and local wallpaper thumbnails with glass surfaces;
  browser-local profile name/photo and guarded conversation deletion.
- Minute-level token activity, 1d/7d/30d selection, explicit missing data and
  visible-interval scaling; no invented usage, throughput or billing values.
- Published file/source views, verified snapshot diffs and retained per-message
  receipts; workbench activity, dictation and composer layout.
- Portuguese preview intent handling and bounded republication recovery,
  preserving explicit no-publication instructions and snapshot verification.
- Read-only Lemonade child health detection so a dead backend is not shown as
  a healthy model or live throughput.

The installer delta only validates and installs the shared task-activity schema
beside ingress. No new runtime version, model, provider, port, privilege mode or
service default is selected by this contribution. Experimental Hermes transport,
identity/backup changes, local deployment records and unused prototype modules
are intentionally excluded. Existing unrelated ODS Hermes services are unchanged.

## Reproducible checks

From `ods/extensions/services/dashboard`:

```sh
npm ci
npm run lint
npm test
npm run build
docker build -t ods-dashboard:ui-review .
```

From `ods`, with the project's Python test dependencies installed:

```sh
node --test extensions/services/pixel-agent/tests/*.test.mjs
python -m pytest extensions/services/pixel-agent/tests/test_workspace_preview.py extensions/services/pixel-edge/tests/test_pixel_edge.py -q
bash tests/test-pixel-host-install.sh
python scripts/audit-extensions.py --project-dir .
```

Run the Node agent suite under Linux/WSL: some cases intentionally use POSIX
processes and sockets. Do not call a stalled native Windows launch a pass.
From `ods/extensions/services/dashboard-api`, run `python -m pytest tests -q`.

### Recorded local results (2026-09-09)

| Check | Evidence |
| --- | --- |
| Clean-lock frontend install, lint, build on Windows | Passed |
| Frontend tests on Windows | 605 passed / 67 files |
| Native Windows Lemonade health boundary tests | 14 passed |
| Production Docker build on Linux amd64 | Passed |
| Pixel plugin tests on Linux/WSL | 955 passed, 1 skipped |
| Preview and edge Python tests on Linux/WSL | 95 passed, 17 subtests |
| Pixel installer fixtures | 251 passed, 0 failed |
| Extension audit | 30 services, 0 errors, 0 warnings |
| Full clean-candidate API suite on Linux/WSL | 2,548 passed, 1 skipped, 2 warnings |

The Dashboard CI matrix now repeats clean install, lint, test and build on
Windows, Ubuntu and macOS. Passing those jobs is **not** a native GPU, browser,
fresh-install or runtime-activation qualification for those operating systems.
Lint now explicitly includes production JSX as well as JavaScript and tests;
the previous flat configuration did not select all production JSX files.

## Browser journeys

The local Windows/Docker/WSL integration exercised actual model generation of a
counter, verified publication, clicks 0 → 1 → 2, a title edit, a second snapshot
and retention of both `+95/-0` and `+2/-2` receipts after reload. Earlier coding
attempts failed when the model issued unsuitable verification commands or kept
executing after publication. They are not erased or generalized into a success.

The separately built clean-candidate frontend was inspected against the local
API/runtime: home, Extensions pagination and starter collections rendered without
the experimental Hermes page. No extension installation or model switch was
performed during these read-only library checks.
Theme selection and a real model reply also passed through that clean frontend;
the installed API/plugin were reused, not presented as a fresh installation.

Before release, repeat these on the intended deployment:

- New browser/session: no seeded profile, chat, theme or fake activity required.
- Create, edit, publish and inspect an actual file; send again and reload; old
  file receipts remain attached to their messages. Test cancellation separately.
- Open/collapse/resize utility panels without losing the chat draft; navigate
  old Pixel/Settings URLs; inspect a long model name and narrow window.
- Select themes, save/remove a photo, reload; verify reduced motion and disabled
  GPU fallback. Profile photos and names stay in that browser's local storage.
- Confirm deletion affects only the selected browser conversation, not workspace
  files or server-side histories. Declining confirmation changes nothing.
- Distinguish absent/stale telemetry from zero consumption. Inspect unsupported
  service/device states; do not install unsupported GPU services to test UI.

## Remaining platform/release gates

No native macOS installation or multi-machine GPU fleet run was performed here.
The parent runtime's Windows Apply/recovery adapter and other parent acceptance
gaps remain explicit; this UI must not imply they are supported or applied.
Retain those release gates and obtain platform-owner acceptance before shipping
the combined runtime broadly. See `../HIGH_RISK_CHANGE_MAP.md` for scope.

Rollback the UI image/source together with the matching parent API/plugin. No
automatic data conversion is performed by this contribution. Existing profile
and conversation storage must not be deleted during rollback. Artwork and
upstream-code terms are recorded in
`../../extensions/services/dashboard/ASSET-NOTICES.md`.
