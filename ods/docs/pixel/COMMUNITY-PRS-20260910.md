# Community PR integration — 10 September 2026

This beta-only composition starts at public-beta `d7d76cdc` and preserves the
existing OpenClaw/Portal work. It does not merge public-beta into main or qualify
a general release.

## Included PRs

[#4055](https://github.com/Osmantic/ODS/pull/4055), [#4061](https://github.com/Osmantic/ODS/pull/4061), [#4066](https://github.com/Osmantic/ODS/pull/4066), [#4074](https://github.com/Osmantic/ODS/pull/4074), [#4075](https://github.com/Osmantic/ODS/pull/4075), [#4076](https://github.com/Osmantic/ODS/pull/4076), [#4077](https://github.com/Osmantic/ODS/pull/4077), [#4078](https://github.com/Osmantic/ODS/pull/4078), [#4079](https://github.com/Osmantic/ODS/pull/4079), [#4080](https://github.com/Osmantic/ODS/pull/4080), [#4081](https://github.com/Osmantic/ODS/pull/4081), [#4082](https://github.com/Osmantic/ODS/pull/4082), [#4083](https://github.com/Osmantic/ODS/pull/4083), [#4084](https://github.com/Osmantic/ODS/pull/4084), [#4085](https://github.com/Osmantic/ODS/pull/4085), [#4092](https://github.com/Osmantic/ODS/pull/4092), [#4093](https://github.com/Osmantic/ODS/pull/4093), [#4102](https://github.com/Osmantic/ODS/pull/4102), [#4103](https://github.com/Osmantic/ODS/pull/4103), [#4105](https://github.com/Osmantic/ODS/pull/4105), [#4106](https://github.com/Osmantic/ODS/pull/4106), [#4107](https://github.com/Osmantic/ODS/pull/4107), [#4108](https://github.com/Osmantic/ODS/pull/4108), [#4109](https://github.com/Osmantic/ODS/pull/4109), [#4110](https://github.com/Osmantic/ODS/pull/4110), [#4112](https://github.com/Osmantic/ODS/pull/4112), [#4113](https://github.com/Osmantic/ODS/pull/4113), [#4114](https://github.com/Osmantic/ODS/pull/4114), [#4115](https://github.com/Osmantic/ODS/pull/4115), [#4116](https://github.com/Osmantic/ODS/pull/4116), [#4117](https://github.com/Osmantic/ODS/pull/4117), [#4118](https://github.com/Osmantic/ODS/pull/4118), [#4119](https://github.com/Osmantic/ODS/pull/4119), [#4120](https://github.com/Osmantic/ODS/pull/4120), [#4121](https://github.com/Osmantic/ODS/pull/4121), [#4139](https://github.com/Osmantic/ODS/pull/4139), [#4140](https://github.com/Osmantic/ODS/pull/4140), [#4141](https://github.com/Osmantic/ODS/pull/4141), [#4142](https://github.com/Osmantic/ODS/pull/4142), [#4143](https://github.com/Osmantic/ODS/pull/4143), [#4144](https://github.com/Osmantic/ODS/pull/4144), [#4145](https://github.com/Osmantic/ODS/pull/4145), [#4146](https://github.com/Osmantic/ODS/pull/4146), [#4147](https://github.com/Osmantic/ODS/pull/4147), [#4148](https://github.com/Osmantic/ODS/pull/4148), [#4149](https://github.com/Osmantic/ODS/pull/4149), [#4150](https://github.com/Osmantic/ODS/pull/4150), [#4151](https://github.com/Osmantic/ODS/pull/4151), [#4152](https://github.com/Osmantic/ODS/pull/4152), [#4153](https://github.com/Osmantic/ODS/pull/4153), [#4154](https://github.com/Osmantic/ODS/pull/4154).

The changes cover retained chat recovery, search, dictation, sharing/settings
race handling, verified files and diffs, conversation organization/export/import,
draft tools, and publication history/responsive preview controls. Import is
text-only and never replays exported jobs or restores permission receipts.

Composition fixes retain current Portal identity, combine overlapping tests and
controls, correct RGB theme variables, and assign distinct React keys to the
file input and draft preview. Real browser inspection caught duplicate file
buttons caused by those sibling keys; a new integration regression covers it.

## Verification

- Frontend: 833 tests passed across 95 files; production build passed.
- Frontend lint: zero errors, 539 warnings (including the existing JSX-unused
  warnings); this is not a claim that lint debt is resolved.
- Focused Windows Python runs: 54 filter/access/cache tests and 50
  Lemonade/talk/router-fixture tests passed.
- Linux container: 49 preview/charset/access tests and 15 subtests passed.
- WSL native ingress: 57 tests passed.

These are source/composition checks, not Windows/Linux/macOS fresh-install,
sharing, cloud-provider, or complete agent acceptance. The existing
[community handoff](COMMUNITY-HANDOFF.md) limitations still apply.

## Initially deferred, completed in the second batch

The remaining 21 PR heads are incorporated with their ancestry:
#4057, #4059, #4060, #4062, #4073, #4087, #4090, #4091, #4104, #4111,
#4124, #4125, #4129, #4131, #4132, #4134, #4135, #4136, #4138, #4165, #4166.

Integration corrections:

- #4111 composes with #4121: malformed records stay in storage and out of the
  sidebar, while valid conversations remain editable/deletable. Unreadable
  top-level JSON still refuses writes. Null records and malformed timestamps
  are covered.
- #4104 withdraws stale access proof and retains failed-mutation errors;
  in-progress mutations do not display the old effective-mode receipt.
- Windows guards execute before POSIX imports and work with unittest as well
  as pytest. Pure access-configuration tests still run on Windows.
- #4073 completes the CLI helper's BSD option handling; source ownership
  checks and refusal to overwrite unrelated commands remain in place.
- PII non-text input raises an explicit error instead of silently replacing
  content with an empty string. Blank environment keys fall back; nonblank
  credentials retain their exact bytes. Session HMAC remains mandatory.
- Numeric helpers reject huge/nonfinite inputs without overflowing; nested
  helpers bound depth/cycles and preserve missing-key records. These are
  standalone utilities, not a change to the token graph or routing policy.
- #4124 installs the conditional timeout dependency only in relevant runtimes,
  handles Python 3.10 timeout exception types, and keeps the SSH tunnel stdlib
  only. The separately qualified advisory-worker minimum remains Python 3.11;
  this patch does not silently lower that runtime's installation requirement.
- #4165 uses a real authenticated host route, not the nonexistent host
  /v1/models route. A private, deadline-bounded worker probes the applied
  leader's saved model endpoint with its own credential, pinned DNS and no
  redirects/proxies. No generation, activation or failover occurs. Changing
  revisions invalidate the result. Online requires the selected model to be
  present; unavailable is distinct from offline and inactive. Managed runtime
  inspection remains Linux/WSL-only, so unsupported hosts return unavailable.
- #4166 exports validated, nonsecret Pixel settings/provider configuration.
  The attachment explicitly excludes credentials, chats and workspace files;
  it is not a complete workspace backup. Invalid or partial replies fail
  without disclosing raw host data. The endpoints are foundations, not new
  auto-failover or backup-restore UI features.

Second-batch verification:

- Frontend: 839 tests / 95 files, build passed; lint 0 errors / 539 warnings.
- Windows Python 3.11: 247 focused dashboard API tests and 95 access/privacy
  tests. All 1,218 tests under ods/tests collected without import errors.
- Linux Python 3.10: 247 API tests (including timeout/cancellation), 71 provider
  tests, 485 agent/edge tests plus 75 subtests (2 existing skips), and 192
  installer/persistence/control tests plus 48 subtests. The expanded health
  suite separately passed 11 tests, including the real private worker.
- Linux Python 3.11: 72 provider/health/tunnel/sharing tests.
- CLI-link tests exercise GNU behavior and simulated BSD option/ownership
  fallback. Installer shell syntax checks passed.

Linux tests used disposable containers, private fixtures and loopback servers.
The Windows-mounted source copy was made non-group-writable in the disposable
container so custody checks tested a valid checkout, not Docker's synthetic
0777 mount permissions. No production custody check was weakened.
No new claim of native macOS or Windows/Linux fresh-install qualification is
made. The community beta remains the place for installed-system testing.
