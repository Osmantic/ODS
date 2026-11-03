# Required-plugin runtime source candidate

Not installed or enabled by ODS. This is a review/build input, not a runtime
distribution, release, deployment recipe, or permission to activate Full Access.
The feature remains dependent on parent PR #3385 and owner-managed installation.

## Complete managed source preparation

`source-lock.json` now binds the required-plugin patch followed by the generic
command-lifecycle patch. The latter supplies `before_command_run`, which the
current ODS managed plugin requires. Applying only the older patch is insufficient.
The resulting complete Git tree is
`dfba3c7a7c74ec7e52774d7f4a8ef376b8536ca7` (reviewed source commit
`45f0f01b0ac47b5e52390b94cf090e58819fe475`). The upstream version remains unchanged.

On a POSIX build host, prepare the source from a local upstream Git repository
containing the exact base commit, without changing that repository or its index:

```sh
python3 prepare.py --upstream-checkout /absolute/path/openclaw \
  --output-parent /absolute/path/private-builds
```

The tool creates a new private directory, verifies both patch hashes, rejects
unsafe archive entries, and compares both the extracted upstream tree and the
fully patched tree with their exact Git identities. It retains failed directories
for diagnosis and never overwrites an earlier preparation. The successful
`source-receipt.json` explicitly records `built: false` and `installed: false`.
It is a build-input receipt, not a protected launcher or runtime attestation.

Use the printed source directory for the pinned dependency installation/build
commands below. Do not apply the patches again in that prepared directory.
No dependency installation, network operation, service change, or activation is
performed by preparation itself. Never point a build at an installed runtime.
Preserve upstream LICENSE and THIRD_PARTY_NOTICES.md when packaging the result.

The command patch separately passed 109 focused regressions, core/test type checks,
lint, a complete build, and isolated real gateway command/startup qualification.
That historical evidence does not qualify a newly prepared or installed build.
Protected artifact delivery, launcher integration, upgrade/rollback, real provider
traffic and cross-platform acceptance remain necessary before activation.

## Provenance and build boundary

- Upstream: https://github.com/openclaw/openclaw
- Exact base: `7af0cfc9c5488e03c4e2f528bdc7ac9f7778b35e` (`v2026.6.33`).
- Patch: `openclaw-2026.6.33-required-plugins.patch`, applied to source only.
- Upstream MIT license is retained in `OPENCLAW-LICENSE`. Preserve the full
  upstream `LICENSE` and `THIRD_PARTY_NOTICES.md` in source/build distributions.
- No dependency versions, lockfiles, installed compiled files, or model pins
  are changed. ODS does not yet consume this patch in its installer.

Qualification uses a new disposable directory containing that exact source:

```sh
git apply --check /absolute/path/openclaw-2026.6.33-required-plugins.patch
git apply /absolute/path/openclaw-2026.6.33-required-plugins.patch
corepack pnpm install --frozen-lockfile --ignore-scripts
corepack pnpm build
OPENCLAW_VITEST_MAX_WORKERS=1 corepack pnpm test \
  src/plugins/required-plugins.test.ts \
  src/agents/agent-tools.required-plugins.test.ts \
  src/gateway/server-plugins.test.ts \
  src/gateway/config-reload.test.ts \
  src/gateway/server-startup-plugins.test.ts \
  src/gateway/server-channels.required-plugins.test.ts \
  src/gateway/server-channels.test.ts \
  src/gateway/server-channels.approval-bootstrap.test.ts \
  src/gateway/channel-health-monitor.test.ts \
  src/infra/approval-handler-bootstrap.test.ts
corepack pnpm tsgo:core
corepack pnpm tsgo:core:test
```

The observed build environment is Linux, Node 22.23.1, repository-pinned pnpm
11.2.2. Dependency installation and build are explicit disposable operations;
never run them in an installed gateway or modify `node_modules` by hand.

## Opt-in launcher contract

`OPENCLAW_REQUIRED_PLUGINS` contains bounded, strict JSON, for example:

```json
{"version":1,"plugins":[{"id":"guard-plugin","hooks":["before_agent_run","before_tool_call"]}]}
```

No value preserves upstream behavior. An empty, malformed, duplicate, unknown
hook, or unsupported-version policy is rejected. The value is captured once;
plugin configuration cannot remove it. This is metadata, never credentials.
Required IDs must also be explicitly enabled in ordinary plugin configuration,
with explicit conversation-hook permission when such hooks are required. This
does not grant permissions or enable a disabled plugin automatically.

The future managed launcher must protect this policy independently of editable
plugin entries and inject it into every covered entrypoint. Clearing an environment
variable before launching a separate unmanaged process is outside this contract.
Do not represent the environment variable itself as an OS privilege boundary.

Managed gateway startup loads full runtime hooks once before its HTTP listener,
after host services and gateway context are prepared. Optional plugin failures
remain optional; service startup retains its later lifecycle. Missing/error
required plugins and absent/non-callable required typed hooks reject startup.
Reload admission stays closed on failure; generation-bound completion prevents
an older load from reopening a newer rejected transition. Required checks also
run at embedded-agent, Pi attempt/stream, and core tool-call boundaries using the
actual composed live hook registry, not individual partial registry setters.

Channel account starts recheck admission at health/retry entry, after asynchronous
account preparation, and at actual deferred transport dispatch. Approval bootstrap
receives a caller-owned admission callback: deferred context registration, retry,
and asynchronous handler construction cannot bypass a later hold. Rejected handler
starts still clean up. Optional approval errors remain nonfatal only while managed
admission is valid. These checks do not interrupt already-dispatched transports.

After repairing a failed required-plugin load, channel recovery uses the existing
health monitor, applicable successful reload, or explicit channel/gateway restart.
When monitoring is disabled or rate-limited, do not assume automatic recovery.
Explicit start after verified repair and manual-stop preservation are tested.

## Runtime proof and remaining work

Candidate patch SHA-256:
`68f327263b8590bc505eda60ad77b03044c07cc4ae1ed957b5a2fdfedc094e52`.
A fresh extraction of the exact upstream archive accepted this patch, and all
fourteen patched source/test files matched the qualification tree byte-for-byte.
The scoped Git LF rule preserves the patch hash in Windows and Linux checkouts
and archives; do not normalize it to CRLF before applying or verifying it.
This revision adds channel and approval-bootstrap admission to the preceding
ten-file candidate; it is a production-source change, not only documentation.

Disposable Linux qualification passed the full source build, production/test
type checks, 32 required-policy tests, three actual core-tool admission tests,
and lint for the preceding ten-file candidate. Gateway regression files also
passed across selected Vitest projects (440 executions, not 440 unique tests).
The new revision passed channel/health/approval regressions, five required-channel
tests, two new deferred-approval regressions, production/test typechecks, and
four-file lint. Six new negative cases were reproduced before their respective
fixes; explicit recovery is an additional positive case. Final full rebuild,
all-fourteen-file lint, and managed/no-policy runtime fixture rechecks passed
for this revision. The final channel/approval group has 92 unique cases across
five files; the runner repeats some gateway cases in multiple projects.

The existing `tests/runtime_provider_activation.integration.mjs` retains its
upstream characterization mode. Set `OPENCLAW_PACKAGE` to a disposable built
candidate and `OPENCLAW_REQUIRED_PLUGIN_TESTS=1` to exercise mandatory startup,
optional-plugin failure, retained-session overrides, rejected reload, and repaired
restart. All inference in that fixture is synthetic loopback traffic; it is not
installed ODS chat acceptance or proof of real cloud use.

Both modes passed against the final fourteen-file built candidate. With the
contract enabled, absent, failed, or incomplete registrations rejected startup with no
legacy inference; an optional plugin failure did not disable the valid guard.
A rejected required-plugin reload admitted no inference, and a verified restart
restored service. The reload fixture deliberately permits one request before
disabling the plugin and one after repair; these are expected admitted calls.
The no-policy mode deliberately retains upstream's unsafe legacy cases for
compatibility characterization. It does not prove managed protection without
the launcher contract. Listener polling is supplemented by source ordering;
polling alone cannot exclude a brief listener opening.

Before installation: complete remaining early-service/cron, direct-outbound and
alternative-harness qualification, protect launcher/source/build custody, wire the shared
native/Edge admission and activation transaction, prove exact rollback and upgrade
behavior, and obtain required target-specific activation authority. The patch
does not implement those installation/activation steps or complete Full Access.

### Offered schema pressure at the 64K precheck

`openclaw-compaction-budget.json` also counts the actual OpenAI conversion of
the final policy-filtered visible tools before Pixel submits a 64K+ completion.
This closes a measured gap: history passed the text-only precheck while schema
JSON caused the transport to reduce the configured output budget silently.
The existing recovery path compacts genuine prior turns, or refuses when fixed
overhead cannot fit. Messages, tools, permissions, reserve, sampler and output
settings are unchanged. The 8K/32K paths retain their prior behavior.

`runtime_schema_pressure.integration.mjs` exercises native recovery and refusal
against a canned loopback provider. It verifies the full 8192 owner output after
compaction; it does not qualify summary quality or model tokenization accuracy.

## Source 4.3.45 read surface and small-context limit

The managed provider-read overlay admits the nine original, policy-filtered
Source reads only at 64K or larger, with exact native plugin version `4.3.45`
and a complete-family 8192-byte schema cap. Older, missing and future versions
retain deferred discovery. The existing 8K four-read surface and 32K surface
are unchanged; native tool metadata preserves the loaded manifest version
through live factories, cached descriptors and wrappers.

A deterministic connected-Source45 8K fixture retained its full qualified lean
system, four read schemas and three discovery controls. Configured output stayed
2048, while the native transport adaptively requested **2036**: estimated input
6155 + output 2036 + one safety token exactly fits 8192. The exact-2048 assertion
failed and remains recorded; this is an accepted 12-token limitation, not a
2048-budget pass. A same-envelope estimator replay with the original Source44
descriptors estimated input 5849 and output 2048; it was never sent to a provider.
No instructions, schemas, permissions, model settings or context limits were
changed to recover those tokens. The separate real 64K local-model candidate
retained the configured 8192 output ceiling and correctly counted the civil-day
fixture. This single targeted success is not a reliability-rate measurement.
