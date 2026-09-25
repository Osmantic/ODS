// Dormant transport adapter for the pinned OpenClaw provider plugin API.
// Not registered by index.js: activation must supply an owner-approved lease
// adapter and set the agent's default to ods-policy/managed with no fallbacks.
// Selection hook errors are swallowed by core. Default model changes alone
// cannot prevent retained session overrides; activation also requires mandatory
// startup registration and actual resolved-model admission below.
import { randomUUID } from 'node:crypto';
import { approveHandoff, handoffCheckpoint, handoffRecipient } from './handoff-approval.mjs';

const PROVIDER = 'ods-policy';
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const RUN = new RegExp(`^(?:chatcmpl_)?${UUID}$`, 'i');
const UNAVAILABLE = Object.freeze({providerOverride: PROVIDER, modelOverride: 'unavailable'});
const unavailable = () => { throw new Error('ODS provider lease unavailable'); };

function leaseSnapshot(value) {
  const match = typeof value?.baseUrl === 'string' && value.baseUrl.match(/^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/v1$/);
  if (!match || Number(match[1]) > 65535 || typeof value.token !== 'string' ||
      !/^[\x21-\x7e]{1,4096}$/.test(value.token) ||
      !Number.isSafeInteger(value.contextTokens) || value.contextTokens < 4096 ||
      !Number.isSafeInteger(value.maxOutputTokens) || value.maxOutputTokens < 1 ||
      value.maxOutputTokens > value.contextTokens || typeof value.reasoning !== 'boolean' ||
      typeof value.supportsVision !== 'boolean') throw new Error('Invalid lease');
  return Object.freeze({baseUrl: value.baseUrl, token: value.token,
    contextTokens: value.contextTokens, maxOutputTokens: value.maxOutputTokens,
    reasoning: value.reasoning, supportsVision: value.supportsVision,
    ...('handoff' in value ? {handoff: handoffRecipient(value.handoff)} : {})});
}

export function createProviderRoutingBridge({agentId = 'pixel', acquireLease, releaseLease, enabled = false,
  durableReplayGuard = false, authorizeHandoff, approvalTimeoutMs = 60000, ownerScopes = false} = {}) {
  if (typeof ownerScopes !== 'boolean') unavailable();
  if (!Number.isSafeInteger(approvalTimeoutMs) || approvalTimeoutMs < 1 || approvalTimeoutMs > 120000) unavailable();
  const runs = new Map();
  const models = new Map();
  let stopped = false, shutdownPromise;
  // Only a host adapter with persistent exclusive claims may prune closed
  // entries. Late SDK retries then receive durable denial, never a fresh route.
  const MAX_RUNS = 256;
  const binding = ctx => ctx?.agentId === agentId && typeof ctx.runId === 'string' &&
    RUN.test(ctx.runId) && typeof ctx.sessionId === 'string' &&
    ctx.sessionId.length > 0 && ctx.sessionId.length <= 256 && (!ownerScopes ||
      typeof ctx.sessionKey === 'string' && /^agent:pixel:openai-user:ods-[a-f0-9]{64}$/.test(ctx.sessionKey));
  const live = entry => !stopped && entry?.state === 'active' && !entry.closed;
  function release(entry) {
    if (!entry.releasePromise) entry.releasePromise = Promise.resolve()
      .then(() => releaseLease({runId: entry.runId, sessionId: entry.sessionId}))
      .then(() => {entry.released = true; entry.lease = undefined;})
      .catch(() => { /* Failed cleanup is not eligible for eviction. */ });
    return entry.releasePromise;
  }
  async function beforeModelResolve(_event, ctx) {
    if (enabled !== true) return undefined;
    if (ctx?.agentId && ctx.agentId !== agentId) return undefined;
    if (stopped) return UNAVAILABLE;
    try {
      if (!binding(ctx) || typeof acquireLease !== 'function' || typeof releaseLease !== 'function') return UNAVAILABLE;
      let entry = runs.get(ctx.runId);
      if (entry && (entry.sessionId !== ctx.sessionId || ownerScopes && entry.sessionKey !== ctx.sessionKey)) return UNAVAILABLE;
      if (!entry) {
        if (durableReplayGuard === true && runs.size >= MAX_RUNS) {
          for (const [oldRun, oldEntry] of runs) {
            if (oldEntry.closed && oldEntry.released) {
              runs.delete(oldRun); models.delete(oldEntry.modelId);
              if (runs.size < MAX_RUNS) break;
            }
          }
        }
        if (runs.size >= MAX_RUNS) return UNAVAILABLE;
        entry = {runId: ctx.runId, sessionId: ctx.sessionId, modelId: `turn-${randomUUID()}`,
          ...(ownerScopes ? {sessionKey: ctx.sessionKey} : {}),
          state: 'pending', closed: false, stopApproval: new AbortController()};
        runs.set(ctx.runId, entry);
        models.set(entry.modelId, entry);
        entry.pending = Promise.resolve()
          .then(() => acquireLease({runId: entry.runId, sessionId: entry.sessionId,
            ...(ownerScopes ? {sessionKey: entry.sessionKey} : {})}))
          .then(async value => {
            const snapshot = leaseSnapshot(value);
            if (entry.closed) { await release(entry); return; }
            entry.lease = snapshot;
            entry.state = 'active';
          }).catch(async () => {
            entry.state = 'failed'; entry.closed = true;
            await release(entry);
          });
      }
      await entry.pending;
      return live(entry) ? {providerOverride: PROVIDER, modelOverride: entry.modelId} : UNAVAILABLE;
    } catch { return UNAVAILABLE; }
  }
  async function agentEnd(_event, ctx) {
    if (enabled !== true) return;
    const entry = runs.get(ctx?.runId);
    if (!entry) return;
    if (!binding(ctx) || entry.sessionId !== ctx.sessionId || ownerScopes && entry.sessionKey !== ctx.sessionKey) return false;
    entry.closed = true;
    entry.stopApproval.abort();
    await entry.pending;
    await release(entry);
    entry.lease = undefined;
    return entry.released === true;
  }
  function shutdown() {
    if (shutdownPromise) return shutdownPromise;
    // Revoke synchronously, before waiting for pending acquisitions or cleanup.
    // A late acquisition is released by its existing closed-entry path.
    stopped = true;
    const entries = [...runs.values()];
    for (const entry of entries) { entry.closed = true; entry.stopApproval.abort(); }
    shutdownPromise = Promise.all(entries.map(async entry => {
      await entry.pending;
      await release(entry);
      return entry.released === true;
    })).then(released => {
      if (released.some(value => !value)) throw new Error('ODS provider cleanup incomplete');
    });
    return shutdownPromise;
  }
  function deny(entry) {
    if (entry) {
      entry.closed = true; entry.stopApproval.abort();
      void Promise.resolve(entry.pending).then(() => release(entry));
    }
    return {outcome: 'block', reason: 'ods-provider-lease-unavailable',
      message: 'The approved Pixel provider route is unavailable. Review routing Settings before retrying.'};
  }
  function beforeAgentRun(event, ctx) {
    if (enabled !== true || (ctx?.agentId && ctx.agentId !== agentId)) return undefined;
    const entry = binding(ctx) ? runs.get(ctx.runId) : undefined;
    if (entry?.sessionId === ctx?.sessionId && (!ownerScopes || entry.sessionKey === ctx?.sessionKey) && live(entry) &&
        ctx.modelProviderId === PROVIDER && ctx.modelId === entry.modelId) {
      if (!entry.lease.handoff) return {outcome: 'pass'};
      try {
        const preview = handoffCheckpoint(event, ctx, entry.lease.handoff);
        if (entry.checkpointDigest && entry.checkpointDigest !== preview.checkpointDigest) return deny(entry);
        entry.checkpointDigest = preview.checkpointDigest;
        entry.approval ??= approveHandoff(preview, authorizeHandoff, entry.stopApproval.signal, approvalTimeoutMs);
        return entry.approval.then(approved => {
          if (!approved || !live(entry)) return deny(entry);
          entry.handoffApproved = true;
          return {outcome: 'pass'};
        });
      } catch { return deny(entry); }
    }
    // The pinned runtime swallows model-selection hook failures, but enforces
    // before_agent_run decisions before submitting inference. Inspect the actual
    // resolved model, not a configured default that a session/cron can override.
    return deny(entry?.sessionId === ctx?.sessionId ? entry : undefined);
  }
  const provider = {
    id: PROVIDER, label: 'ODS routing', auth: [],
    resolveSyntheticAuth: () => ({apiKey: 'ods-policy-placeholder', source: 'ods-policy', mode: 'api-key'}),
    async prepareRuntimeAuth(ctx) {
      const entry = models.get(ctx?.modelId);
      if (!live(entry)) return {apiKey: 'ods-policy-unavailable', baseUrl: 'http://127.0.0.1:1/v1'};
      return {apiKey: entry.lease.token, baseUrl: entry.lease.baseUrl};
    },
    resolveDynamicModel(ctx) {
      const entry = models.get(ctx?.modelId);
      const lease = live(entry) ? entry.lease : undefined;
      return {id: ctx?.modelId || 'unavailable', provider: PROVIDER, api: 'openai-completions',
        name: lease ? 'ODS managed turn' : 'ODS unavailable',
        baseUrl: lease?.baseUrl ?? 'http://127.0.0.1:1/v1', reasoning: lease?.reasoning ?? false,
        input: lease?.supportsVision ? ['text', 'image'] : ['text'],
        // Required SDK placeholders, not a displayed estimate of provider prices.
        cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
        contextWindow: lease?.contextTokens ?? 4096, maxTokens: lease?.maxOutputTokens ?? 1};
    },
    wrapStreamFn(ctx) {
      return (model, context, options) => {
        const entry = models.get(ctx?.modelId);
        if (!live(entry) || typeof ctx?.streamFn !== 'function' || options?.signal?.aborted ||
            (entry.lease.handoff && entry.handoffApproved !== true) ||
            model?.provider !== PROVIDER || model?.id !== ctx.modelId) return unavailable();
        return ctx.streamFn({...model, id: 'ods/pixel', baseUrl: entry.lease.baseUrl}, context,
          {...options, apiKey: entry.lease.token});
      };
    },
  };
  // The runtime otherwise times modifying hooks out after 15 seconds, shorter
  // than an owner's advertised review window. This is a bounded registration
  // option, not an extension of the provider lease or agent-run deadline.
  return {provider, beforeModelResolve, beforeAgentRun, agentEnd, shutdown,
    beforeAgentRunOptions: Object.freeze({timeoutMs: approvalTimeoutMs + 5000})};
}
