import {createHash} from 'node:crypto';
import {integrationRequest, snapshotIntegrationRequest, INTEGRATION_TOOL_NAMES} from './integrations.mjs';

// Native outcome evidence and refusal after uncertainty. Never executes,
// reads approvals, parses model prose or requests another model attempt.
const PENDING = 'This call did not run the action; it requested approval. Review Settings → Portal integrations, then ask Portal to retry the same action.';
const UNKNOWN = 'Its result could not be verified; check the destination before retrying.';
const READY = 'This exact action already has owner approval, but this review-only recovery did not run it or use the approval. Send a new request to execute it.';
export const MAX_INTEGRATION_NOTICE = 2048;
const identity = value => typeof value === 'string' && value.length > 0 && value.length <= 512 && !/[\x00-\x20\x7f]/.test(value);
const names = new Set(INTEGRATION_TOOL_NAMES);
const actions = new Set(['notes-write', 'notify', 'mcp-call']);
const SAFE_APP_READS = Object.freeze({'app:documents':['search','read'], 'app:photos':['search'],
  'app:watch':['list'], 'app:news':['unread','digest']});
const UNRESOLVED = 'An earlier integration call in this response has an unverified result. This additional action was not submitted. Check the destination, then send a new request before trying again. Known built-in reads and the catalog remain available.';
const effectful = payload => payload?.action === 'notify' || payload?.action === 'notes-write' ||
  payload?.action === 'mcp-call' && !(Object.hasOwn(SAFE_APP_READS,payload.server) && SAFE_APP_READS[payload.server].includes(payload.tool));
const childOf = (id,call) => {
  const parent = call.id.replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0,120) || 'call';
  const prefix = `tool_search_code:${parent}:${call.name}:`;
  return call.deferred && id.startsWith(prefix) && /^[1-9][0-9]*$/.test(id.slice(prefix.length));
};

function requestKey(payload) {
  const json = JSON.stringify(snapshotIntegrationRequest(payload));
  return createHash('sha256').update(json).digest('hex');
}

const effectiveKey = (baseKey, reviewOnly) => reviewOnly
  ? createHash('sha256').update(JSON.stringify({request:baseKey,recoveryReviewOnly:true})).digest('hex') : baseKey;

function label(payload) {
  if (payload.action === 'notify') return 'Notification';
  if (payload.action === 'notes-write') return payload.mode === 'create' ? 'Create an Obsidian note' : 'Add to an Obsidian note';
  const apps = {'app:documents': 'Documents', 'app:photos': 'Photos', 'app:watch': 'Watched pages', 'app:news': 'News'};
  const app = Object.hasOwn(apps,payload.server) ? apps[payload.server] : undefined;
  // No free-form server/tool names, note paths, URLs, titles, messages or
  // arguments enter owner delivery. The ordinal identifies the exact call.
  const tools = {search:'search', read:'read', add:'add', remove:'remove', list:'list', digest:'digest', asset:'asset'};
  return app ? `${app}${Object.hasOwn(tools, payload.tool) ? ` · ${tools[payload.tool]}` : ''}` : 'Custom MCP tool';
}

export function appendIntegrationNotice(text, notice) {
  if (typeof text !== 'string' || typeof notice !== 'string' || !notice) return text;
  return text.endsWith(notice) ? text : `${text}${text.trim() ? '\n\n' : ''}${notice}`;
}

export function createIntegrationOutcomes({maximumRuns = 64, maximumCalls = 64} = {}) {
  if (![maximumRuns, maximumCalls].every(value => Number.isInteger(value) && value >= 1 && value <= 256)) throw new Error('invalid-outcome-limits');
  const runs = new Map(), bySession = new Map(), byKey = new Map(), reservations = new WeakMap();
  let generation = 0;
  const owned = run => run && runs.get(run.id) === run && !run.invalid &&
    bySession.get(run.sessionId) === run.id && (!run.sessionKey || byKey.get(run.sessionKey) === run.id);
  const current = run => owned(run) && !run.cancelled;
  function bind(run, context, includeCancelled = false) {
    if (context?.agentId !== 'pixel' || !identity(context.sessionId) || context.sessionId !== run.sessionId ||
        context.runId !== undefined && context.runId !== run.id ||
        context.sessionKey !== undefined && (!identity(context.sessionKey) || !context.sessionKey.startsWith('agent:pixel:'))) return false;
    if (context.sessionKey) {
      if (run.sessionKey && run.sessionKey !== context.sessionKey) {run.invalid = true; return false;}
      if (!run.sessionKey) {
        const newer = runs.get(byKey.get(context.sessionKey));
        if (newer && newer.generation > run.generation) {run.invalid = true; return false;}
        run.sessionKey = context.sessionKey;
        byKey.set(run.sessionKey, run.id);
      }
    }
    return includeCancelled ? owned(run) : current(run);
  }
  function begin(_event, context, ownerRunId) {
    if (context?.agentId !== 'pixel' || !identity(context.runId) || !identity(context.sessionId) ||
        context.sessionKey !== undefined && (!identity(context.sessionKey) || !context.sessionKey.startsWith('agent:pixel:'))) return;
    let run = runs.get(context.runId);
    if (run) {
      if (bind(run, context)) run.ended = false;
      return;
    }
    const prior = runs.get(byKey.get(context.sessionKey)) ?? runs.get(bySession.get(context.sessionId));
    const parent = identity(ownerRunId) && ownerRunId !== context.runId ? runs.get(ownerRunId) : undefined;
    const uncertain = parent?.uncertain === true || prior?.uncertain === true && context.trigger !== 'user';
    while (runs.size >= maximumRuns) {
      const [id, old] = runs.entries().next().value;
      runs.delete(id);
      if (bySession.get(old.sessionId) === id) bySession.delete(old.sessionId);
      if (byKey.get(old.sessionKey) === id) byKey.delete(old.sessionKey);
    }
    run = {id:context.runId, sessionId:context.sessionId, sessionKey:context.sessionKey, calls:[], sequence:0,
      generation:++generation, uncertain};
    runs.set(run.id, run);
    bySession.set(run.sessionId, run.id);
    if (run.sessionKey) byKey.set(run.sessionKey, run.id);
  }
  function reviewStartClear(context) {
    // A query cannot adopt a sparse context, rebind an owner, reopen a run or
    // clear uncertainty. In particular, do not call the mutating bind().
    if (context?.agentId !== 'pixel' || ![context.runId,context.sessionId,context.sessionKey].every(identity)) return false;
    const run = runs.get(context.runId);
    return Boolean(current(run) && !run.ended && !run.uncertain && !run.overflow
      && run.sessionId === context.sessionId && run.sessionKey === context.sessionKey && run.calls.length === 0);
  }
  function before(event, context, decision, trustedControl) {
    const run = runs.get(context?.runId), id = context?.toolCallId ?? event?.toolCallId;
    if (!run || !bind(run, context, true) || !run.sessionKey) return;
    // This floor only tightens native admission. A later missing/false flag,
    // changed model arguments or same-run fallback cannot remove it.
    if (trustedControl?.recoveryReviewOnly === true) {
      run.reviewOnly = true;
      for (const call of run.calls) if (!call.taken) {
        call.recoveryReviewOnly = true;
        call.key = effectiveKey(call.baseKey,true);
      }
    }
    if (decision?.block || !identity(id)) return;
    const params = decision?.params ?? event?.params;
    let name = event?.toolName;
    const deferred = name === 'tool_call';
    if (deferred) {
      name = params?.id;
      if (typeof name === 'string' && name.startsWith('openclaw:pixel-ods:')) name = name.slice('openclaw:pixel-ods:'.length);
    }
    if (!names.has(name)) return;
    try {
      const payload = integrationRequest(name, deferred ? params.args : params, context);
      if (!actions.has(payload?.action)) return;
      if (run.uncertain && effectful(payload)) return {block:true, blockReason:UNRESOLVED};
      if (run.ended || run.cancelled) return;
      if (run.calls.some(call => call.id === id || call.nativeChild === id)) {run.invalid = true; return;}
      const baseKey = requestKey(payload);
      // The pinned Tool Search dispatch emits native hooks for both the wrapper
      // and its concrete child. They share one admitted action, not two tickets.
      if (!deferred) {
        const parents = run.calls.filter(call => call.pending && !call.taken && !call.nativeChild &&
          call.name === name && childOf(id,call));
        if (parents.length === 1) {
          if (parents[0].baseKey !== baseKey) {
            parents[0].pending = false;
            parents[0].closed = true;
            return {block:true,blockReason:'The integration arguments changed between native wrapper and child admission. No action was submitted.'};
          }
          parents[0].nativeChild = id;
          return;
        }
        if (parents.length > 1) {run.invalid = true; return;}
      }
      if (run.calls.length >= maximumCalls) {run.overflow = true; return;}
      const recoveryReviewOnly = run.reviewOnly === true;
      run.calls.push({id, name, deferred, baseKey,
        key:effectiveKey(baseKey,recoveryReviewOnly),
        recoveryReviewOnly, label:label(payload), sequence:++run.sequence,
        run, pending:true, taken:false, status:null});
    } catch { /* Malformed/unbounded inputs receive no observation, never new authority. */ }
  }
  function take(id, payload, context) {
    if (!identity(id) || !actions.has(payload?.action)) return undefined;
    let key;
    try {key = requestKey(payload);} catch {return undefined;}
    const candidates = [];
    for (const run of runs.values()) {
      if (!bind(run, context) || run.ended || !run.sessionKey) continue;
      for (const call of run.calls) {
        if (!call.pending || call.taken || call.baseKey !== key) continue;
        if (call.nativeChild ? call.nativeChild === id : call.id === id || childOf(id,call)) candidates.push(call);
      }
    }
    if (candidates.length !== 1) return undefined;
    const call = candidates[0], token = Object.freeze({});
    call.taken = true;
    reservations.set(token, call);
    return token;
  }
  function complete(token, status) {
    const call = token && reservations.get(token);
    if (!call) return;
    reservations.delete(token);
    if (!current(call.run) || call.closed || call.run.ended || !['pending', 'unknown', 'settled', 'ready'].includes(status)) return;
    call.status = status;
    if (status === 'unknown') call.run.uncertain = true;
  }
  function control(token) {
    const call = token && reservations.get(token);
    if (!call || !current(call.run) || call.run.ended || call.closed) return undefined;
    return call.recoveryReviewOnly ? {recoveryReviewOnly:true} : {};
  }
  function after(event, context) {
    const run = runs.get(context?.runId);
    if (!run || !bind(run, context)) return;
    const id = context?.toolCallId ?? event?.toolCallId;
    const call = run.calls.find(item => item.id === id || item.nativeChild === id);
    if (call) call.pending = false;
  }
  function end(event, context) {
    const run = runs.get(context?.runId ?? event?.runId);
    if (!run || !bind(run, context)) return;
    run.ended = true;
    for (const call of run.calls) if (call.status === null) {call.closed = true; call.pending = false;}
    if (event?.success === false) run.cancelled = true;
  }
  function cancelUser(user) {
    if (typeof user !== 'string') return;
    for (const run of runs.values()) if (run.sessionKey === `agent:pixel:openai-user:${user}`) run.cancelled = true;
  }
  function projection(id) {
    const run = runs.get(id);
    if (!current(run)) return undefined;
    const settled = new Map();
    const pending = new Map();
    for (const call of run.calls) if (call.status === 'settled') settled.set(call.key, Math.max(settled.get(call.key) ?? 0, call.sequence));
    for (const call of run.calls) if (call.status === 'pending' || call.status === 'ready') {
      if ((pending.get(call.key)?.sequence ?? 0) < call.sequence) pending.set(call.key, call);
    }
    const visible = run.calls.filter(call => call.status === 'unknown' || ['pending','ready'].includes(call.status) &&
      pending.get(call.key) === call && call.sequence > (settled.get(call.key) ?? 0));
    if (!visible.length && !run.overflow) return undefined;
    const lines = visible.slice(0, 6).map(call => `${call.label} (request ${call.sequence}): ${call.status === 'pending' ? PENDING : call.status === 'ready' ? READY : UNKNOWN}`);
    if (visible.length > 6 || run.overflow) lines.push('Additional integration call statuses are not shown in this notice.');
    return lines.join('\n\n').slice(0, MAX_INTEGRATION_NOTICE);
  }
  function refusal(payload, context) {
    if (!effectful(payload) || context?.agentId !== 'pixel' || !identity(context.sessionId) || !identity(context.sessionKey)) return undefined;
    const run = runs.get(bySession.get(context.sessionId));
    if (!owned(run) || run.sessionKey !== context.sessionKey || context.runId !== undefined && context.runId !== run.id) return undefined;
    return run.uncertain ? UNRESOLVED : undefined;
  }
  function verification(base, id) {
    const notice = projection(id);
    return notice ? {...base, integrationNotice:notice} : base;
  }
  function channel(event, decision) {
    if (event?.kind !== 'final' || decision?.cancel) return decision;
    const notice = projection(event?.runId), payload = decision?.payload ?? event?.payload;
    if (!notice || typeof payload?.text !== 'string') return decision;
    return {...decision, payload:{...payload, text:appendIntegrationNotice(payload.text, notice)},
      reason:decision?.reason ?? 'Portal adds the recorded status of this integration call.'};
  }
  return {begin, reviewStartClear, before, take, control, complete, after, end, cancelUser, projection, verification, channel, refusal, effectful,
    clear() {runs.clear(); bySession.clear(); byKey.clear();}};
}
