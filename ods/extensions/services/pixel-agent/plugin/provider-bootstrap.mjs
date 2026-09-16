// Owner-custody composition only: no registration, config writes or activation.
// The parent launcher must qualify/protect the runtime, source and deployment
// inputs and enforce required hooks. Editable plugin preferences are not authority.
import { posix } from 'node:path';
import { createProviderRoutingBridge } from './provider-routing.mjs';
import { createLeaseWorkerAdapter } from './provider-lease-worker.mjs';
import { createHandoffOwnerAdapter } from './handoff-owner-worker.mjs';

const fail = () => { throw new Error('ODS managed provider bootstrap unavailable'); };
const CLOSED = Object.freeze({providerOverride: 'ods-policy', modelOverride: 'unavailable'});
const BLOCK = Object.freeze({outcome: 'block', reason: 'ods-provider-bootstrap-unavailable',
  message: 'The approved Pixel provider activation is unavailable. Review runtime readiness.'});
const MODEL = {primary: 'ods-policy/managed', fallbacks: []};
const PROVIDER = {baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions', apiKey: 'ods-policy-unavailable',
  models: [{id: 'managed', name: 'ODS managed', contextWindow: 32768, maxTokens: 4096, reasoning: false, input: ['text']}]};
// OpenClaw's runtime config adds these exact defaults before plugin registration.
// Admit the raw placeholder or that normalized form, never a projection that
// discards unknown endpoint, auth, model or transport options.
const NORMALIZED_PROVIDER = {...PROVIDER, models: [{...PROVIDER.models[0],
  cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, api: PROVIDER.api}]};
export const managedProviderRequiredHooks = Object.freeze(['before_model_resolve', 'before_agent_run', 'agent_end']);

function object(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail();
  return value;
}
function canonical(value, depth = 0) {
  if (depth > 32) fail();
  if (value === null || typeof value === 'string' || typeof value === 'boolean' ||
      typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + Array.from(value, item => canonical(item, depth + 1)).join(',') + ']';
  const entries = Object.entries(Object.getOwnPropertyDescriptors(object(value)));
  if (Reflect.ownKeys(value).length !== entries.length) fail();
  return '{' + entries.sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, descriptor]) => {
    if (!descriptor.enumerable || !('value' in descriptor)) fail();
    return JSON.stringify(key) + ':' + canonical(descriptor.value, depth + 1);
  }).join(',') + '}';
}
function exactKeys(value, keys) {
  if (Object.keys(object(value)).sort().join(',') !== [...keys].sort().join(',')) fail();
}
function path(value) {
  if (typeof value !== 'string' || value.length > 4096 || !posix.isAbsolute(value) || value === '/' ||
      value.includes('\0') || posix.normalize(value) !== value) fail();
  return value;
}
function integer(value, min, max) { if (!Number.isSafeInteger(value) || value < min || value > max) fail(); }
function binding(value) {
  exactKeys(value, ['schemaVersion', 'activationId', 'revision', 'allowCloud']);
  if (value.schemaVersion !== 1 || typeof value.activationId !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.activationId) ||
      typeof value.allowCloud !== 'boolean') fail();
  integer(value.revision, 0, Number.MAX_SAFE_INTEGER);
}

export function createManagedProviderBootstrap({deployment, readConfig,
  createLease = createLeaseWorkerAdapter, createHandoff = createHandoffOwnerAdapter,
  createBridge = createProviderRoutingBridge} = {}) {
  if (typeof readConfig !== 'function' || typeof createLease !== 'function' ||
      typeof createHandoff !== 'function' || typeof createBridge !== 'function') fail();
  // Copy once: the caller cannot change command paths or policy after construction.
  const encoded = canonical(deployment);
  if (Buffer.byteLength(encoded) > 16384) fail();
  const owned = JSON.parse(encoded);
  exactKeys(owned, ['binding', 'sourceRoot', 'hostPython', 'providerDirectory', 'ownerScopes',
    'leaseTimeoutSeconds', 'approvalTimeoutSeconds']);
  binding(owned.binding);
  path(owned.sourceRoot); path(owned.hostPython); path(owned.providerDirectory);
  if (typeof owned.ownerScopes !== 'boolean') fail();
  integer(owned.leaseTimeoutSeconds, 1, 3600);
  integer(owned.approvalTimeoutSeconds, 1, 120);
  if (owned.leaseTimeoutSeconds <= owned.approvalTimeoutSeconds) fail();
  const expectedBinding = canonical(owned.binding);
  function currentAuthority() {
    const root = object(readConfig());
    if (Buffer.byteLength(canonical(root)) > 1024 * 1024) fail();
    const agents = object(root.agents);
    if (!Array.isArray(agents.list) || agents.list.some(agent => !agent || typeof agent !== 'object' || Array.isArray(agent))) fail();
    const matches = agents.list.filter(agent => agent.id === 'pixel');
    if (matches.length !== 1) fail();
    const agent = matches[0];
    const plugin = object(object(object(root.plugins).entries)['pixel-ods']);
    const provider = canonical(object(object(root.models).providers)['ods-policy']);
    if (plugin.enabled !== true || object(plugin.hooks).allowConversationAccess !== true ||
        canonical(object(plugin.config).managedProvider) !== expectedBinding ||
        canonical(agent.model) !== canonical(MODEL) ||
        ![canonical(PROVIDER), canonical(NORMALIZED_PROVIDER)].includes(provider)) fail();
    for (const scope of [root, agent]) {
      if (Object.keys(object(object(scope.tools ?? {}).byProvider ?? {})).length) fail();
    }
    // Provider switching cannot silently adopt changed tools, workspace or host.
    // Access runtime still owns actual admission and OS-level enforcement.
    const defaults = object(agents.defaults ?? {});
    return canonical({globalTools: root.tools ?? null, agentTools: agent.tools ?? null,
      sandbox: agent.sandbox ?? null, defaultSandbox: defaults.sandbox ?? null,
      workspace: agent.workspace ?? null, defaultWorkspace: defaults.workspace ?? null});
  }
  const authority = currentAuthority();
  const authorizations = new Map();
  let stopped = false, closing, bridge;
  function shutdown() {
    stopped = true;
    if (!closing) {
      try { closing = Promise.all([bridge.shutdown(), ...authorizations.keys()])
        .then(() => {}, () => {throw new Error('ODS provider cleanup incomplete');}); }
      catch { closing = Promise.reject(new Error('ODS provider cleanup incomplete')); }
      // Drift may first be observed from a synchronous stream hook. Retain the
      // rejecting promise for the parent to await without an unhandled rejection.
      void closing.catch(() => {});
    }
    return closing;
  }
  function valid() {
    if (stopped) return false;
    try { if (currentAuthority() === authority) return true; } catch { /* generic denial */ }
    void shutdown();
    return false;
  }
  const worker = createLease({
    command: [owned.hostPython, '-I', '-B', posix.join(owned.sourceRoot, 'bin/ods-pixel-route-lease')],
    directory: owned.providerDirectory, ownerScopes: owned.ownerScopes,
    request() {
      if (!valid()) fail();
      return Object.freeze({expectedRevision: owned.binding.revision, allowCloud: owned.binding.allowCloud,
        confirmed: true, timeoutSeconds: owned.leaseTimeoutSeconds});
    },
  });
  if (worker?.durableReplayGuard !== true || typeof worker.acquireLease !== 'function' ||
      typeof worker.releaseLease !== 'function') fail();
  const authorize = createHandoff({command: [owned.hostPython, '-I', '-B',
    posix.join(owned.sourceRoot, 'bin/pixel_provider/handoff_worker.py')],
  directory: owned.providerDirectory, timeoutSeconds: owned.approvalTimeoutSeconds});
  if (typeof authorize !== 'function') fail();
  bridge = createBridge({enabled: true, ownerScopes: owned.ownerScopes, durableReplayGuard: true,
    approvalTimeoutMs: owned.approvalTimeoutSeconds * 1000,
    acquireLease: async ctx => { if (!valid()) fail(); return await worker.acquireLease(ctx); },
    releaseLease: ctx => worker.releaseLease(ctx),
    authorizeHandoff: ctx => {
      if (!valid()) return null;
      // Approval denial may settle before the owner worker has reaped its
      // child. Track the transport itself so shutdown cannot claim cleanup early.
      const pending = Promise.resolve().then(() => valid() ? authorize(ctx) : null);
      authorizations.set(pending, {runId: ctx.checkpoint.runId, sessionId: ctx.checkpoint.sessionId});
      return pending.then(receipt => {
        authorizations.delete(pending); // resolved transport proves child exit
        return valid() ? receipt : null;
      }, () => {
        // Retain failed transport evidence. Deleting it would let a later
        // agentEnd report successful cleanup and incorrectly clear access.
        void shutdown();
        throw new Error('ODS provider cleanup incomplete');
      });
    },
  });
  for (const method of ['shutdown', 'beforeModelResolve', 'beforeAgentRun', 'agentEnd']) {
    if (typeof bridge?.[method] !== 'function') fail();
  }
  const otherAgent = ctx => typeof ctx?.agentId === 'string' && ctx.agentId !== 'pixel';
  const provider = Object.freeze({...bridge.provider,
    async prepareRuntimeAuth(ctx) {
      if (!valid() || otherAgent(ctx)) fail();
      const result = await bridge.provider.prepareRuntimeAuth(ctx);
      if (!valid()) fail();
      return result;
    },
    resolveDynamicModel(ctx) {
      if (!valid() || otherAgent(ctx)) fail();
      return bridge.provider.resolveDynamicModel(ctx);
    },
    wrapStreamFn(ctx) {
      if (!valid() || otherAgent(ctx)) fail();
      const stream = bridge.provider.wrapStreamFn(ctx);
      return (model, context, options) => {
        if (!valid()) fail();
        return stream(model, context, options);
      };
    },
  });
  return Object.freeze({provider, requiredHooks: managedProviderRequiredHooks,
    beforeAgentRunOptions: bridge.beforeAgentRunOptions, shutdown,
    async beforeModelResolve(event, ctx) {
      if (otherAgent(ctx)) return undefined;
      if (!valid()) return CLOSED;
      const selected = await bridge.beforeModelResolve(event, ctx);
      return valid() ? selected : CLOSED;
    },
    async beforeAgentRun(event, ctx) {
      if (otherAgent(ctx)) return undefined;
      if (!valid()) return BLOCK;
      const decision = await bridge.beforeAgentRun(event, ctx);
      return valid() ? decision : BLOCK;
    },
    // Always clean up even after drift. The parent also calls this when its
    // access-first admission rejects a selected run before provider admission.
    async agentEnd(event, ctx) {
      if (await bridge.agentEnd(event, ctx) === false) throw new Error('ODS provider cleanup incomplete');
      const pending = [...authorizations].filter(([, owner]) => owner.runId === ctx?.runId && owner.sessionId === ctx?.sessionId)
        .map(([promise]) => promise);
      try { await Promise.all(pending); }
      catch { throw new Error('ODS provider cleanup incomplete'); }
    },
  });
}
