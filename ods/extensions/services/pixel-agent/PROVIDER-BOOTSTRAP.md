# Managed provider bootstrap (source candidate, not installed)

`plugin/provider-bootstrap.mjs` composes the existing bridge, prepared lease
worker and checkpoint owner worker. It does not register a plugin, edit config,
grant permissions, install the required-runtime patch or activate saved preferences.
Parent #3385 owns those lifecycle/registration changes and shared access admission.

## Exact factory contract

```js
import { createManagedProviderBootstrap } from './provider-bootstrap.mjs';

const routing = createManagedProviderBootstrap({
  deployment: {
    binding: {schemaVersion: 1, activationId, revision, allowCloud},
    sourceRoot,             // absolute, custody-verified ODS source root
    hostPython,             // absolute, owner-qualified host Python
    providerDirectory,      // absolute, private provider store
    ownerScopes: true,      // canonical ODS ingress sessionKey only
    leaseTimeoutSeconds: 180,
    approvalTimeoutSeconds: 60,
  },
  readConfig: readAuthoritativeCurrentConfig, // synchronous strict JSON object
});
```

All fields are required. Binding matches the actual Python `plan_activation`
projection exactly. Lease lifetime is 1..3600 seconds and must exceed the
1..120-second approval window. Neither extends the agent's own deadline. The
factory copies deployment inputs once; no model, request header, HTTP body or
editable plugin option can supply executable paths or substitute the binding.
Fixed commands are `[hostPython, '-I', '-B', sourceRoot + '/bin/ods-pixel-route-lease']`
and the corresponding `/bin/pixel_provider/handoff_worker.py`. The lease launcher
rechecks prepared runtime source/tree/interpreter custody. Parent must independently
qualify the host interpreter, source tree, private store and launcher provenance.
String validation is not a filesystem/OS privilege boundary.

Optional dependency-injection keys `createLease`, `createHandoff`, `createBridge`
default to the real modules. They are trusted composition/test seams, never
serializable owner API inputs. Lease factories must expose `acquireLease`,
`releaseLease`, `durableReplayGuard: true`; handoff factories return the existing
authorization callback. Bridge factories must expose the existing provider/hooks,
registration timeout and the new `shutdown()` lifecycle. `agentEnd` returns
`false` for incomplete cleanup or a mismatched identity on an existing run; the
bootstrap promotes that to a generic error without clearing access accounting.
Constructors must not
start workers; work begins only from actual bound hooks.

Returned surface: `provider`, `beforeModelResolve`, `beforeAgentRun`, `agentEnd`,
`beforeAgentRunOptions`, `requiredHooks`, `shutdown`. No self-registration or
module-global singleton is created. Parent must retain one qualified instance
for each active managed gateway generation, share it across registration passes,
and close it before replacing that generation. Do not create a fresh factory
for each hook, request or metadata-only registry load.

## Minimal parent registration composition (review sketch, not applied)

```js
const finishManagedRun = async (event, context) => {
  await routing.agentEnd(event, context); // throws if lease cleanup is unknown
  accessRuntime.finish({runId: event.runId}, context);
};
api.registerProvider(routing.provider);
api.on('before_model_resolve', routing.beforeModelResolve);
api.on('before_agent_run', async (event, context) => {
  try {
    const access = await accessRuntime.admit(undefined, context);
    if (access?.outcome !== 'pass') {
      await finishManagedRun(event, context);
      return access ?? {outcome: 'block', reason: 'access-admission-unavailable'};
    }
    const decision = await routing.beforeAgentRun(event, context);
    if (decision?.outcome === 'block') await finishManagedRun(event, context);
    return decision ?? access;
  } catch {
    try { await finishManagedRun(event, context); }
    catch { /* Retain busy/failed access state for operator reconciliation. */ }
    return {outcome: 'block', reason: 'managed-admission-unavailable'};
  }
}, routing.beforeAgentRunOptions);
api.on('agent_end', finishManagedRun);
```

This replaces/composes the parent's existing admission/end callbacks; do not
register duplicate independent callbacks or remove probe handling, tool guards,
access checks or other existing hooks. Parent must review actual access result
semantics and preserve them. Non-Pixel agents pass through the routing hooks.
Do not depend on core emitting `agent_end` after admission denial. Close the
selected lease and finish access accounting on that path explicitly; repeated
matching-run cleanup is idempotent. If cleanup is unknown, retain the access hold
rather than advertising idle. Parent tests must cover denial with and without a
later `agent_end`, and an unknown-cleanup failure that never clears admission.
Provider hooks reject an explicitly foreign agent. Scoped Pixel routing requires
the exact native `sessionKey` described in `PROVIDER-SCOPES.md`, independently
of `sessionId` and `runId`; unknown channels fail closed in this opt-in mode.

The manifest must declare `providers: ['ods-policy']` and validate the exact
`managedProvider` binding schema (no extra keys). Registration options must retain
`timeoutMs = approvalTimeoutSeconds * 1000 + 5000`. Parent's protected launcher
must require at least `before_model_resolve`, `before_agent_run`, `agent_end` plus
its own required access/tool hooks through `OPENCLAW_REQUIRED_PLUGINS`. That is an
environment contract in the qualified source-built runtime, NOT `plugins.required`
configuration. It does not work on unpatched upstream merely because the version
number is the same. Missing/disabled/unregistered hooks must prevent startup.

## Drift and shutdown

The factory checks exactly one Pixel agent, enabled conversation hooks, binding,
Pixel model `ods-policy/managed` with no native fallbacks and the exact closed
placeholder provider. Global/Pixel `tools.byProvider` policies are rejected.
Declared tool configuration, sandbox and workspace defaults/overrides are pinned;
unrelated logging and other-agent changes are not rewritten or globally rejected.
Checks run before/after asynchronous selection/admission/auth and immediately
before actual stream dispatch. An observed mismatch or read failure permanently
closes this instance. Restoring original bytes does not revive it. Runtime reload
and source/permission changes still require the parent's shared admission barrier;
this getter is not a filesystem watcher or proof of effective OS permissions.

`shutdown()` synchronously revokes all routes and aborts pending approval, then
waits for pending acquisitions, lease release and owner-transport exit. Repeated
calls return the same promise. Incomplete lease cleanup rejects; never report
rollback complete from that rejection. A transport that has already dispatched
may have performed work; shutdown does not undo effects or charges.
`agentEnd()` also waits for that run/session's owner transport after lease cleanup,
not merely its faster aborted approval decision. It does not wait for another
run's checkpoint worker. A rejected owner transport means cleanup is unknown:
retain its failed record, close the bootstrap and keep subsequent matching cleanup
and shutdown rejected. A resolved `null` is normal denial after known transport
exit, not that failure. Dependency-injected transports must preserve this contract.
Busy access accounting prevents mode transitions but
does not by itself disable ordinary concurrent runs; do not describe it as a
global admission hold. Parent owns explicit hold/recovery semantics.

Before first activation, capture and verify the private presence-aware baseline
from `plan_activation`; retain it independently of editable preferences. Never
construct a rollback baseline from already activated configuration.
Parent lifecycle order: close shared admission, await `routing.shutdown()`, prove
owned workers have stopped, restore the presence-aware activation baseline, then
independently qualify restored routing/access before reopening. Do not restore
config first or create a replacement instance while cleanup is unknown. Stop
hooks, failed access admission and interrupted startup/reload must use this
lifecycle. No source module alone supplies that installed transaction.

## Qualification

Unit tests cover exact binding/defaults, malformed deployment, caller mutation,
native identity, fixed private commands, drift between admission and dispatch,
late acquisition, access-denial cleanup, idempotent shutdown, unknown cleanup
and waiting for owner transport exit after approval has already been aborted.
`provider-access-composition.test.mjs` uses the real access-runtime with the real
bootstrap and recording worker seams. It proves access-first hold denial, provider
denial with absent/duplicate end events, slow/unknown cleanup retaining activity,
foreign identity rejection, per-run checkpoint exit, independent runs and detached
tool accounting. Rejected transports before and during cleanup retain unknown
activity and never become successful cleanup through deletion. Its executable parent sketch is test-only
`tests/fixtures/managed-admission.mjs`; the parent's actual entrypoint is unchanged.

The existing pinned gateway integration accepts `ODS_MANAGED_BOOTSTRAP=1` with
`ODS_PREPARE_LEASE_RUNTIME=1`, `ODS_HANDOFF_OWNER_API=1`, `ODS_OWNER_SCOPE=task`
and the documented `OPENCLAW_PACKAGE`/`ODS_PROVIDER_WORKER_PYTHON`. It constructs
the actual Python activation projection, uses the real factory/private workers
and required-plugin contract, and exercises approval, denial, tools/history,
return, drift denial and refusal to revive after restoring configuration bytes.
The managed fixture now composes the real access-runtime, acquires an authenticated
transition hold between model turns, proves denial without inference and retention
of the hold, explicitly releases it, then completes the handoff journey without
leaking access activity. It does not exercise privileged mode switching or real
host tools; those remain parent-owned installed acceptance.
The disposable plugin is a fixture, not the parent's installed `index.js`.
Model responses and owner authentication are synthetic. Real installed ODS chat,
fresh install/upgrade/rollback, Full Access/admin/native qualification and the
joint fleet acceptance requirements remain open.
