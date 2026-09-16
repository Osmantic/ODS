// Registration composition, not installation authority. The parent launcher must
// qualify the deployment environment, source/runtime bytes and executable paths.
// Never derive executable authority from editable plugin configuration.
import {createManagedProviderBootstrap} from './provider-bootstrap.mjs';
import {createManagedCommandAdmission} from './managed-command-admission.mjs';

export const DEPLOYMENT_ENV = 'PIXEL_ODS_PROVIDER_DEPLOYMENT';
export const requiredManagedHooks = Object.freeze([
  'before_model_resolve', 'before_agent_run', 'agent_end',
  'before_command_run', 'before_tool_call', 'after_tool_call', 'gateway_stop',
]);
const blocked = () => ({outcome: 'block', reason: 'managed-admission-unavailable'});
const unavailable = () => ({providerOverride: 'ods-policy', modelOverride: 'unavailable'});
const error = () => new Error('ODS managed runtime unavailable or cleanup incomplete');
const own = (value, key) => value && Object.hasOwn(value, key);

function canonical(value, depth = 0) {
  if (depth > 32) throw error();
  if (value === null || ['string', 'boolean'].includes(typeof value) ||
      typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(item => canonical(item, depth + 1)).join(',') + ']';
  if (!value || typeof value !== 'object' ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) throw error();
  return '{' + Object.keys(value).sort().map(key => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor?.enumerable || !('value' in descriptor)) throw error();
    return JSON.stringify(key) + ':' + canonical(descriptor.value, depth + 1);
  }).join(',') + '}';
}

function authority(config) {
  const agents = config?.agents?.list;
  if (!Array.isArray(agents)) throw error();
  const pixels = agents.filter(agent => agent?.id === 'pixel');
  if (pixels.length !== 1) throw error();
  const pixel = pixels[0], defaults = config.agents.defaults ?? {};
  const plugin = config.plugins?.entries?.['pixel-ods'];
  const value = canonical({enabled: plugin?.enabled ?? null, hooks: plugin?.hooks ?? null,
    binding: plugin?.config?.managedProvider ?? null,
    provider: config.models?.providers?.['ods-policy'] ?? null,
    model: pixel.model ?? null, globalTools: config.tools ?? null,
    agentTools: pixel.tools ?? null, sandbox: pixel.sandbox ?? null,
    defaultSandbox: defaults.sandbox ?? null, workspace: pixel.workspace ?? null,
    defaultWorkspace: defaults.workspace ?? null});
  if (Buffer.byteLength(value) > 1024 * 1024) throw error();
  return value;
}

function requireHooks(raw) {
  if (typeof raw !== 'string' || Buffer.byteLength(raw) > 16384) throw error();
  let policy;
  try { policy = JSON.parse(raw); } catch { throw error(); }
  const entries = policy?.plugins?.filter(entry => entry.id === 'pixel-ods');
  if (policy?.version !== 1 || entries?.length !== 1 ||
      !Array.isArray(entries[0].hooks) ||
      requiredManagedHooks.some(hook => !entries[0].hooks.includes(hook))) throw error();
}

export function createManagedRuntimeRegistry({environment = process.env,
  createRouting = createManagedProviderBootstrap,
  createCommands = createManagedCommandAdmission, controlTimeoutMs = 5000} = {}) {
  if (!Number.isSafeInteger(controlTimeoutMs) || controlTimeoutMs < 1 || controlTimeoutMs > 60000) throw error();
  let current, poisoned = false;
  const registered = new WeakSet();
  function refuse() {
    poisoned = true;
    if (current) void current.shutdown().catch(() => {});
    throw error();
  }
  function register(api, accessRuntime) {
    // Metadata/tool discovery must not instantiate or dispose the active owner.
    if (api.registrationMode !== 'full') {
      if (api.registrationMode === undefined &&
          (own(api.pluginConfig, 'managedProvider') || environment[DEPLOYMENT_ENV] !== undefined)) refuse();
      return null;
    }
    const raw = environment[DEPLOYMENT_ENV];
    const requested = own(api.pluginConfig, 'managedProvider');
    if (poisoned) throw error();
    if (!requested && raw === undefined) {
      if (current) refuse();
      return null;
    }
    if (!requested || typeof raw !== 'string' || !raw || Buffer.byteLength(raw) > 16384 ||
        typeof api.runtime?.config?.current !== 'function' ||
        !['registerProvider', 'on', 'registerRuntimeLifecycle'].every(key => typeof api[key] === 'function')) refuse();
    if (accessRuntime.status().available !== true) refuse();
    try { requireHooks(environment.OPENCLAW_REQUIRED_PLUGINS); } catch { refuse(); }
    if (current) {
      if (current.accessRuntime !== accessRuntime || current.deploymentText !== raw ||
          canonical(api.pluginConfig.managedProvider) !== current.binding || !current.valid()) refuse();
    } else {
      try {
        const deployment = JSON.parse(raw);
        if (canonical(deployment.binding) !== canonical(api.pluginConfig.managedProvider)) refuse();
        const readConfig = () => api.runtime.config.current();
        const baseline = authority(readConfig());
        const routing = createRouting({deployment, readConfig});
        const commands = createCommands({accessRuntime});
        const selected = new Map(), selecting = new Set();
        let closed = false, failed = false, cleanupFailed = false, closing;
        const isPixel = context => !(typeof context?.agentId === 'string' && context.agentId !== 'pixel');
        const probe = context => accessRuntime.isProbe(context) === true;
        function shutdown() {
          closed = true;
          if (!closing) {
            const close = fn => {try { return Promise.resolve(fn()); } catch { return Promise.reject(error()); }};
            closing = Promise.all([close(() => routing.shutdown()), close(() => commands.shutdown())])
              .then(() => { selected.clear(); }, () => {failed = true; throw error();});
            void closing.catch(() => {});
          }
          return closing;
        }
        function valid() {
          if (closed || failed) return false;
          try {
            if (accessRuntime.status().available !== true) throw error();
            requireHooks(environment.OPENCLAW_REQUIRED_PLUGINS);
            if (environment[DEPLOYMENT_ENV] === raw && authority(readConfig()) === baseline) return true;
          } catch { /* A missing/ambiguous current snapshot cannot authorize work. */ }
          failed = true; void shutdown(); return false;
        }
        async function finish(event, context) {
          if (cleanupFailed) throw error();
          try {
            const owner = selected.get(context?.runId ?? event?.runId);
            if (owner && ['runId', 'sessionId', 'sessionKey', 'agentId'].some(key => owner[key] !== (context?.[key] ?? null))) throw error();
            if (!probe(context)) await routing.agentEnd(event, context);
            accessRuntime.finish({runId: event?.runId}, context);
            selected.delete(context?.runId ?? event?.runId);
          } catch { cleanupFailed = true; failed = true; void shutdown(); throw error(); }
        }
        async function admit(event, context) {
          if (!valid()) return blocked();
          try {
            const access = accessRuntime.admit(undefined, context);
            if (access?.outcome !== 'pass') { await finish(event, context); return access ?? blocked(); }
            const decision = probe(context) ? access : await routing.beforeAgentRun(event, context);
            if (!valid() || decision?.outcome === 'block') {
              await finish(event, context); return blocked();
            }
            return decision ?? access;
          } catch {
            try { await finish(event, context); } catch { /* Do not clear an unknown owner hold. */ }
            return blocked();
          }
        }
        async function select(event, context) {
          if (!valid()) return unavailable();
          if (probe(context)) return undefined;
          if (!isPixel(context)) return routing.beforeModelResolve(event, context);
          if (!['idle', 'busy'].includes(accessRuntime.status().phase)) return unavailable();
          if (typeof context?.runId !== 'string' || !context.runId) return unavailable();
          const pending = {sessionKey: context.sessionKey};
          selecting.add(pending);
          const identity = Object.fromEntries(['runId', 'sessionId', 'sessionKey', 'agentId'].map(key => [key, context[key] ?? null]));
          const previous = selected.get(context.runId);
          if (previous && canonical(previous) !== canonical(identity)) {
            selecting.delete(pending); failed = true; void shutdown(); return unavailable();
          }
          selected.set(context.runId, identity);
          try {
            const decision = await routing.beforeModelResolve(event, context);
            if (decision?.providerOverride === 'ods-policy' && decision.modelOverride === 'unavailable') {
              await finish(event, context);
            }
            return valid() ? decision : unavailable();
          } catch {
            try { await finish(event, context); } catch { /* Keep uncertain cleanup held. */ }
            return unavailable();
          } finally { selecting.delete(pending); }
        }
        function assertTransition() {
          const command = commands.status();
          if (!valid() || selected.size || selecting.size || command.active || command.unknown || command.closed) throw error();
        }
        function status() {
          const base = accessRuntime.status(), command = commands.status();
          if (!valid() || command.unknown || command.closed) return {...base, available: false, phase: 'unavailable', revision: null};
          // Preserve the public access status shape. Count extra reservations
          // conservatively; never expose the deployment, route or credentials.
          const active = Math.max(base.active, command.active, selected.size + selecting.size);
          return {...base, active, phase: active && base.phase === 'idle' ? 'busy' : base.phase};
        }
        function heldControlSnapshot() {
          const base = accessRuntime.status(), command = commands.status();
          if (base.available !== true || base.phase !== 'held' || base.active !== 0 ||
              selected.size || selecting.size || cleanupFailed || command.active || command.unknown) throw error();
          return base;
        }
        async function readControlStatus() {
          const normal = status();
          if (normal.available) return normal;
          let timer;
          try {
            // Configuration replacement intentionally invalidates the old
            // provider. Preserve ONLY an already-held management channel, and
            // only after that same provider/command owner has fully drained.
            // This never revives registration, inference, tools, or probes.
            const before = heldControlSnapshot();
            await Promise.race([shutdown(), new Promise((_, reject) => {
              timer = setTimeout(() => reject(error()), controlTimeoutMs);
            })]);
            const after = heldControlSnapshot();
            if (after.revision !== before.revision || after.pid !== before.pid) throw error();
            return after;
          } catch { return {...accessRuntime.status(), available: false, phase: 'unavailable', revision: null}; }
          finally { clearTimeout(timer); }
        }
        async function acquireTransition(token, revision) {
          if (valid()) {
            assertTransition();
          } else {
            const held = await readControlStatus();
            if (!held.available || held.phase !== 'held' || accessRuntime.owns(token) !== true) throw error();
          }
          return accessRuntime.acquire(token, revision);
        }
        current = {accessRuntime, deploymentText: raw, binding: canonical(deployment.binding),
          routing, commands, valid, shutdown, admit, finish, select, assertTransition, status,
          readControlStatus, acquireTransition,
          readRegistration() {
            assertTransition();
            // Report this successfully registered owner, not an editable config
            // echo. This does not attest any provider request or model response.
            return {status: 'active', binding: JSON.parse(current.binding)};
          },
          beforeCommandRun(event, context) {
            if (!valid()) return {action: 'block', reason: 'ods-command-admission-unavailable'};
            return commands.beforeCommandRun(event, context);
          },
          cleanup(context) {
            if (context?.sessionKey || context?.runId) {
              // Session reset/delete is not proof that its runner or subprocess
              // exited. Do not release activity or shut down unrelated sessions.
              if (selecting.size || [...selected.values()].some(owner =>
                owner.runId === context.runId || owner.sessionKey === context.sessionKey)) throw error();
              return;
            }
            return shutdown();
          }};
      } catch { refuse(); }
    }
    if (!registered.has(api)) {
      try {
        api.registerProvider(current.routing.provider);
        api.on('before_model_resolve', current.select);
        api.on('before_agent_run', current.admit, current.routing.beforeAgentRunOptions);
        api.on('agent_end', current.finish, {timeoutMs: 125000});
        api.on('before_command_run', current.beforeCommandRun, {timeoutMs: 6000});
        api.on('gateway_stop', current.shutdown, {timeoutMs: 125000});
        api.registerRuntimeLifecycle({id: 'ods-managed-runtime', cleanup: current.cleanup});
        registered.add(api);
      } catch { refuse(); }
    }
    return current;
  }
  return {register};
}
