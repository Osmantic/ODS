// Process-local delivery custody, not a second subagent scheduler. Only native
// hook receipts can link an owner request to a later announced parent answer.
// Nothing here reads transcripts, starts a run, or grants a tool permission.
import {silentReplyText} from './owner-visible-reply.mjs';
import {createHash} from 'node:crypto';

const ORIGINAL = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const USER = /^ods-[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const string = value => typeof value === 'string' && value.length > 0 && value.length <= 512 && !/[\x00-\x1f\x7f]/.test(value);
const FAILED = 'Portal could not confirm the delegated response. Review saved work before continuing; no operation was replayed.';
const kind = 'ods-subagent-delivery';
const registries = new WeakMap();

export function delegationAccessIdentity(config,state,{posix=typeof process.getuid==='function'}={}) {
  // Access coordination intentionally permits ordinary chat on Windows and
  // unqualified runtimes. Preserve that policy; held/interrupted or genuinely
  // failed POSIX custody never becomes an allowed delegation epoch.
  if (!state || state.initialization_failure || !(['idle','busy'].includes(state.phase)
      || !posix && state.phase==='unavailable')) return null;
  return createHash('sha256').update(JSON.stringify(config)).digest('hex');
}

export function subagentDeliveryFor(guard, options) {
  if (!registries.has(guard)) registries.set(guard, createSubagentDelivery(options));
  return registries.get(guard);
}

export function createSubagentDelivery({agentId = 'pixel', now = Date.now,
  maximumRuns = 64, maximumChildren = 32, ttlMs = 32 * 60 * 1000,
  accessIdentity = () => 'fixture', resolveOwnerSession = () => undefined,
  verificationForRun = () => ({status:'none'}), abortSession = async () => false,
  finalText = () => undefined} = {}) {
  const roots = new Map(), runs = new Map(), spawned = new Map();
  const prefix = `agent:${agentId}:openai-user:`;
  const childPrefix = `agent:${agentId}:subagent:`;
  const childKey = value => typeof value === 'string' && value.startsWith(childPrefix) && UUID.test(value.slice(childPrefix.length));
  const fail = chain => { chain.failed = true; chain.ready = null; };
  function access() { try { return accessIdentity(); } catch { return null; } }
  function valid(chain) {
    if (chain.failed || now() - chain.started > ttlMs || !chain.access || access() !== chain.access) { fail(chain); return false; }
    return true;
  }
  function remove(chain) {
    roots.delete(chain.id);
    for (const [id, run] of runs) if (run.chain === chain) runs.delete(id);
  }
  function expire() {
    for (const chain of roots.values()) if (now() - chain.started > ttlMs) remove(chain);
    for (const [key, record] of spawned) if (now() - record.at > ttlMs) spawned.delete(key);
  }
  function owned(context) {
    const run = context?.agentId === agentId ? runs.get(context.runId) : undefined;
    if (!run) return null;
    if (context.sessionId && context.sessionId !== run.chain.sessionId || context.sessionKey && run.chain.sessionKey && context.sessionKey !== run.chain.sessionKey) {
      fail(run.chain); return null;
    }
    if (context.sessionKey && !run.chain.sessionKey) run.chain.sessionKey = context.sessionKey;
    return valid(run.chain) ? run : null;
  }
  function ownerMatches(chain, user) {
    const key = prefix + user;
    if (!USER.test(user) || chain.sessionKey && chain.sessionKey !== key) return false;
    // Sparse no-tool prompt hooks may omit the key. Native session identity,
    // never prompt/completion text, supplies the missing owner binding.
    try {
      if (resolveOwnerSession(key)?.sessionId !== chain.sessionId) return false;
    } catch { return false; }
    chain.sessionKey = key;
    return true;
  }
  function observe(_event, context) {
    expire();
    if (context?.agentId !== agentId || !string(context.runId) || !string(context.sessionId)) return;
    const prior = runs.get(context.runId);
    if (prior) { const run = owned(context); if (run) {run.candidate = null; run.ended = false;} return; }
    const provenance = context.inputProvenance;
    if (provenance?.kind === 'inter_session' && provenance.sourceTool === 'subagent_announce' && childKey(provenance.sourceSessionKey)) {
      const matches = [...roots.values()].filter(chain => valid(chain) && chain.sessionId === context.sessionId
        && chain.sessionKey && chain.sessionKey === context.sessionKey && chain.children.has(provenance.sourceSessionKey));
      if (matches.length !== 1) return;
      const chain = matches[0];
      if (chain.ready || chain.continuations >= maximumChildren * 2) {fail(chain); return;}
      const child = chain.children.get(provenance.sourceSessionKey);
      // Native provenance identifies the source; enforce its exact native
      // announcement run identity too, not an arbitrary inter-session run.
      if (context.runId !== `announce:v1:${provenance.sourceSessionKey}:${child.runId}`) return;
      for (const run of runs.values()) if (run.chain===chain) run.candidate=null;
      chain.currentRun=context.runId;
      child.announced = true; chain.continuations++;
      runs.set(context.runId,{chain,id:context.runId,calls:new Map(),yielded:false,candidate:null,ended:false});
      return;
    }
    if (provenance || context.trigger !== 'user' || !ORIGINAL.test(context.runId)) return;
    if (context.sessionKey && !(context.sessionKey.startsWith(prefix) && USER.test(context.sessionKey.slice(prefix.length)))) return;
    for (const chain of roots.values()) if (chain.sessionId === context.sessionId) {
      const releasable=!chain.children.size || chain.delivered && chain.ready;
      fail(chain); if (releasable) remove(chain);
    }
    if (roots.size >= maximumRuns) {
      for (const chain of roots.values()) if ((!chain.children.size && (chain.failed || chain.delivered))
          || chain.delivered && chain.ready) remove(chain);
    }
    if (roots.size >= maximumRuns) return; // Never evict a live owner's request.
    const chain = {id:context.runId,sessionId:context.sessionId,sessionKey:context.sessionKey,
      started:now(),access:access(),children:new Map(),continuations:0,delegated:false,failed:false,ready:null,currentRun:context.runId};
    roots.set(chain.id,chain);
    runs.set(chain.id,{chain,id:chain.id,calls:new Map(),yielded:false,candidate:null,ended:false});
  }
  function before(event, context, decision) {
    const run = owned(context), name = context?.toolName ?? event?.toolName;
    if (!run || decision?.block || !['sessions_spawn','sessions_yield'].includes(name)) return;
    const callId = context.toolCallId ?? event.toolCallId;
    if (!string(callId) || run.calls.size >= 128) {fail(run.chain); return;}
    const params = decision?.params ?? event.params;
    if (name === 'sessions_spawn' && (params?.runtime === 'acp' || params?.mode === 'session')) return;
    run.calls.set(callId,name);
  }
  function nativeSpawn(event, context) {
    expire();
    if (!UUID.test(event?.runId ?? '') || !childKey(event.childSessionKey)
        || context?.childSessionKey !== event.childSessionKey || context?.runId !== event.runId
        || !string(context.requesterSessionKey)) return;
    if (spawned.size >= maximumRuns * maximumChildren) return;
    spawned.set(event.childSessionKey,{runId:event.runId,parent:context.requesterSessionKey,at:now()});
  }
  function after(event, context) {
    const run = owned(context), callId = context?.toolCallId ?? event?.toolCallId;
    if (!run) return;
    const name = run.calls.get(callId); run.calls.delete(callId);
    if (!name || (context.toolName ?? event.toolName) !== name || event.error || event.result?.isError) return;
    const result = event.result?.details;
    if (name === 'sessions_spawn' && result?.status === 'accepted' && childKey(result.childSessionKey) && UUID.test(result.runId ?? '')) {
      const record = spawned.get(result.childSessionKey);
      if (!record || record.runId !== result.runId || record.parent !== run.chain.sessionKey) {fail(run.chain); return;}
      if (run.chain.children.size >= maximumChildren) {fail(run.chain); return;}
      const previous = run.chain.children.get(result.childSessionKey);
      if (previous && previous.runId !== result.runId) {fail(run.chain); return;}
      if (!previous) run.chain.children.set(result.childSessionKey,{runId:result.runId,announced:false});
    }
    if (name === 'sessions_yield' && result?.status === 'yielded') {
      run.yielded = true; run.candidate = null; run.chain.delegated = true;
      if (!run.chain.children.size) fail(run.chain);
    }
  }
  function finalize(event, context, decision) {
    const run = owned(context);
    if (!run || run.id === run.chain.id || run.id !== run.chain.currentRun || run.yielded || decision?.action === 'revise') return;
    const text = event?.lastAssistantMessage;
    if (typeof text !== 'string' || silentReplyText(text) || Buffer.byteLength(text) > 256 * 1024
        || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text)) {fail(run.chain); return;}
    run.candidate = text;
  }
  function end(event, context) {
    const run = owned(context);
    if (!run || run.yielded || run.id !== run.chain.currentRun) return;
    if (event?.success === false || event.error) {fail(run.chain); return;}
    // Existing conversation-hook permission already supplies the native
    // terminal message. Examine only its public text/stop reason; never retain
    // or expose a transcript. success:true alone also occurs on provider errors.
    const terminal = Array.isArray(event.messages) ? event.messages.findLast(message => message?.role === 'assistant') : undefined;
    if (['error','aborted'].includes(terminal?.stopReason)) {fail(run.chain); return;}
    if (!run.candidate) {
      if (run.id !== run.chain.id && ['stop','end_turn'].includes(terminal?.stopReason)) fail(run.chain);
      return;
    }
    if (event?.success !== true || !terminal || !['stop','end_turn'].includes(terminal.stopReason)) {fail(run.chain); return;}
    const pieces = Array.isArray(terminal.content) ? terminal.content.filter(block => block?.type === 'text') : [];
    let publicText;
    try {publicText=finalText(terminal);} catch {fail(run.chain); return;}
    if (!pieces.length || pieces.some(block => typeof block.text !== 'string' || Buffer.byteLength(block.text) > 256 * 1024)
        || pieces.reduce((total,block)=>total + Buffer.byteLength(block.text),0) > 256 * 1024
        || typeof publicText!=='string' || publicText.trim() !== run.candidate.trim()) {fail(run.chain); return;}
    if (![...run.chain.children.values()].every(child => child.announced)) {run.candidate = null; return;}
    run.ended = true;
    run.chain.ready = {runId:run.id,text:run.candidate};
    run.candidate=null;run.calls.clear();
  }
  function read(user, runId) {
    expire();
    const base = {schemaVersion:1,kind,runId};
    const chain = roots.get(runId);
    if (!ORIGINAL.test(runId ?? '') || !chain || !ownerMatches(chain,user) || !valid(chain)) return {...base,status:'interrupted',message:FAILED};
    if (!chain.delegated) {chain.delivered = true; return {...base,status:'not-delegated'};}
    if (!chain.ready) return {...base,status:'waiting'};
    // The finalization hooks verified THIS parent continuation, never a child
    // summary nor the original yielded introduction. Keep its private run ID
    // server-side so existing public verification schemas remain unchanged.
    const verification = verificationForRun(chain.ready.runId);
    chain.delivered = true;
    return {...base,status:'ready',text:chain.ready.text,verification};
  }
  async function cancel(user) {
    const selected = [...roots.values()].filter(chain => USER.test(user) && chain.sessionKey === prefix + user && (chain.delegated || chain.children.size));
    const keys = new Set();
    for (const chain of selected) {fail(chain); keys.add(chain.sessionKey); for (const key of chain.children.keys()) keys.add(key);}
    const results = await Promise.all([...keys].map(async key => {try {return await abortSession(key) === true;} catch {return false;}}));
    return {tracked:selected.length > 0,aborted:results.length > 0 && results.every(Boolean)};
  }
  function finalRun(user,runId) {
    const chain=roots.get(runId);
    return chain && ownerMatches(chain,user) && valid(chain) ? chain.ready?.runId : undefined;
  }
  function blocked(context) {
    if (context?.agentId !== agentId) return;
    // After restart/expiry there is no trusted owner request to continue. A
    // native announcement may still arrive, but cannot resume its mutations
    // solely because it carries an old session key. Other native chats retain
    // their existing admission rules.
    if (typeof context.runId==='string' && context.runId.startsWith(`announce:v1:${childPrefix}`)
        && typeof context.sessionKey==='string' && context.sessionKey.startsWith(prefix)
        && !runs.has(context.runId)) return {block:true,blockReason:FAILED};
    for (const chain of roots.values()) {
      const child=chain.children.get(context.sessionKey);
      const related=child?.runId===context.runId || context.sessionId===chain.sessionId &&
        (context.runId===chain.id || [...chain.children].some(([key,value])=>context.runId===`announce:v1:${key}:${value.runId}`));
      if (related && !valid(chain)) return {block:true,blockReason:FAILED};
    }
  }
  return {observe,before,after,nativeSpawn,finalize,end,read,finalRun,cancel,blocked,
    invalidate:() => {for (const chain of roots.values()) fail(chain);}};
}
