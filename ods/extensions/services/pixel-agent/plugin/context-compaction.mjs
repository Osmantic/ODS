// Owner-scoped metadata and native summarization. Never read/return transcript
// text, accept a session key from HTTP, truncate history, or abort an active run.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

const USER = /^ods-[a-f0-9]{64}$/;
const REQUEST = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const HASH = /^[a-f0-9]{64}$/;
const PROCESS_INSTANCE = crypto.randomUUID();
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const integer = value => Number.isSafeInteger(value) && value >= 0 && value <= 100000000 ? value : null;
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 512 &&
  !/[\u0000-\u001f\u007f]/.test(value) ? value : null;
const idle = count => ({status:'idle', requestId:null, tokensBefore:null, tokensAfter:null, reason:null, count});
const ERROR = () => new Error('context coordination unavailable');
export async function prepareStableContextModel({entry}) {
  if (entry?.providerOverride === 'ods-policy' || entry?.modelProvider === 'ods-policy') {
    throw Object.assign(ERROR(),{code:'unsupported-model'});
  }
  return {close:async () => {}};
}

function modelFor(config, entry, agentId) {
  const agent = config?.agents?.list?.find(item => item?.id === agentId);
  const selected = agent?.model?.primary ?? agent?.model ?? config?.agents?.defaults?.model?.primary ?? config?.agents?.defaults?.model;
  const split = typeof selected === 'string' ? selected.indexOf('/') : -1;
  const provider = text(entry?.providerOverride) ?? (split > 0 ? text(selected.slice(0,split)) : text(entry?.modelProvider));
  const runtimeId = text(entry?.modelOverride) ?? (split > 0 ? text(selected.slice(split+1)) : text(entry?.model));
  const configured = config?.models?.providers?.[provider]?.models?.find(item => item?.id === runtimeId);
  const alias = provider === 'ods-gateway' && ['ods/current','default'].includes(runtimeId);
  const label = runtimeId === 'ods/current' ? 'Current' : 'Default';
  const match = alias && typeof configured?.name === 'string' ? configured.name.match(new RegExp(`^ODS ${label} \\((.+)\\)$`)) : null;
  const id = alias ? text(match?.[1]) : runtimeId;
  // Match the qualified SDK's agent-over-default context cap. Session metadata
  // can supply a missing model capacity, but never override a smaller cap.
  // The plugin's modelContextWindow is prompt metadata, not native usage proof.
  const capacity = integer(configured?.contextTokens) || integer(configured?.contextWindow) || integer(entry?.contextTokens) || null;
  const cap = integer(agent?.contextTokens ?? config?.agents?.defaults?.contextTokens) || null;
  const window = capacity && cap ? Math.min(capacity,cap) : capacity;
  const route = alias ? config?.plugins?.entries?.['pixel-ods']?.config?.modelRouteFingerprint : undefined;
  if (route !== undefined && (typeof route !== 'string' || route.length !== 64 || !HASH.test(route))) throw ERROR();
  const routeFingerprint = route ?? null;
  const identity = [provider,runtimeId,id,window];
  if (routeFingerprint) identity.push(routeFingerprint);
  return {provider,id,window,routeFingerprint,
    revision:hash(JSON.stringify(identity))};
}

export async function readContextRequest(req, compact = false) {
  if (req.method !== 'POST') return {status:405};
  if (!/^application\/json(?:\s*;|$)/i.test(req.headers?.['content-type'] ?? '')) return {status:415};
  const chunks = []; let bytes = 0;
  try {
    for await (const chunk of req) {
      const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      bytes += value.length;
      if (bytes > 512) return {status:413};
      chunks.push(value);
    }
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!body || typeof body !== 'object' || Array.isArray(body) ||
        Object.keys(body).sort().join(',') !== (compact ? 'request_id,user' : 'user') ||
        !USER.test(body.user ?? '') || compact && !REQUEST.test(body.request_id ?? '')) return {status:400};
    return {status:200, user:body.user, ...(compact ? {requestId:body.request_id} : {})};
  } catch { return {status:400}; }
}

export function createContextCompaction({agentId = 'pixel', readSession, readConfig = () => ({}),
  callGateway, admission, activeSession = () => false,
  directory = path.join(process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'), '.ods-context-compactions'),
  now = () => Date.now(), instanceId = PROCESS_INSTANCE, timeoutMs = 1920000,
  prepareModel = prepareStableContextModel, maximumRequests = 128, requireModelObservation = true} = {}) {
  if (!/^[a-z0-9_-]{1,64}$/.test(agentId) || typeof readSession !== 'function' ||
      typeof callGateway !== 'function' || !admission || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 ||
      timeoutMs > 1920000 || !Number.isSafeInteger(maximumRequests) || maximumRequests < 1 || maximumRequests > 512) throw ERROR();
  const pending = new Map();
  const modelAttempts = new Map();
  const keyFor = user => { if (!USER.test(user ?? '')) throw ERROR(); return `agent:${agentId}:openai-user:${user}`; };

  function check(target, isDirectory = false) {
    const stat = fs.lstatSync(target);
    if (stat.isSymbolicLink() || (isDirectory ? !stat.isDirectory() : !stat.isFile() || stat.nlink !== 1) ||
        typeof process.getuid === 'function' && (stat.uid !== process.getuid() || (stat.mode & 0o077))) throw ERROR();
    return stat;
  }
  function ensureDirectory() {
    if (!fs.existsSync(directory)) fs.mkdirSync(directory, {mode:0o700, recursive:true});
    check(directory, true);
  }
  function read(user) {
    const filename = path.join(directory, `${user}.json`);
    if (!fs.existsSync(directory)) return {version:1, operations:[]};
    check(directory, true);
    if (!fs.existsSync(filename)) return {version:1, operations:[]};
    if (check(filename).size > 262144) throw ERROR();
    const value = JSON.parse(fs.readFileSync(filename, 'utf8'));
    if (value?.version !== 1 || !Array.isArray(value.operations) || value.operations.length > maximumRequests) throw ERROR();
    if (value.maintenance != null && (!HASH.test(value.maintenance.leaseToken ?? '') || !text(value.maintenance.instance))) throw ERROR();
    if (value.measurement != null && (!HASH.test(value.measurement.modelRevision ?? '') || !HASH.test(value.measurement.sessionRevision ?? '') ||
        integer(value.measurement.used) === null || !integer(value.measurement.window) || !Number.isSafeInteger(value.measurement.measuredAt) ||
        value.measurement.compactionCount !== undefined && integer(value.measurement.compactionCount) === null)) throw ERROR();
    const requests = new Set();
    for (const item of value.operations) {
      if (!item || !REQUEST.test(item.requestId ?? '') || requests.has(item.requestId) ||
          !['running','completed','skipped','failed','unknown'].includes(item.status) || !HASH.test(item.leaseToken ?? '') ||
          !text(item.instance) || !HASH.test(item.sessionRevision ?? '') ||
          ![null, 'not-needed','missing-transcript','runtime-failed','result-unconfirmed','runtime-restarted',
            'coordination-failed','unsupported-model','runtime-busy'].includes(item.reason) ||
          ![item.tokensBefore,item.tokensAfter].every(n => n === null || integer(n) !== null)) throw ERROR();
      requests.add(item.requestId);
    }
    return value;
  }
  function save(user, value) {
    ensureDirectory();
    const filename = path.join(directory, `${user}.json`);
    if (fs.existsSync(filename)) check(filename);
    const temporary = path.join(directory, `.${user}-${crypto.randomUUID()}.tmp`);
    let fd;
    try {
      fd = fs.openSync(temporary, 'wx', 0o600);
      fs.writeFileSync(fd, JSON.stringify(value)); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
      fs.renameSync(temporary, filename);
      if (process.platform !== 'win32') {
        const dir = fs.openSync(directory, 'r'); try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
      }
    } finally { if (fd !== undefined) fs.closeSync(fd); if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  }
  function entryFor(user) { return readSession({agentId, sessionKey:keyFor(user)}); }
  const revisionFor = (user, entry) => text(entry?.sessionId) ? hash(`${keyFor(user)}\0${entry.sessionId}`) : null;
  function recover(user, ledger) {
    let changed = false;
    if (ledger.maintenance && ledger.maintenance.instance !== instanceId) {
      if (admission.owns(ledger.maintenance.leaseToken)) admission.release(ledger.maintenance.leaseToken);
      delete ledger.maintenance; changed = true;
    }
    for (const item of ledger.operations) {
      if (item.instance === instanceId || !['running','unknown'].includes(item.status)) continue;
      // A new gateway incarnation proves its previous in-process RPC has died.
      // Release only our exact private lease, never another owner's transition.
      if (admission.owns(item.leaseToken)) admission.release(item.leaseToken);
      item.status = 'unknown'; item.reason = 'runtime-restarted'; item.instance = instanceId; changed = true;
    }
    if (changed) save(user, ledger);
  }
  function project(user, entry, ledger, preferred) {
    const model = modelFor(readConfig(),entry,agentId), {provider,id,window} = model;
    const sessionRevision = revisionFor(user, entry);
    if (ledger.measurement && (ledger.measurement.modelRevision !== model.revision || ledger.measurement.sessionRevision !== sessionRevision ||
        requireModelObservation && !['model-call-v2','native-compaction'].includes(ledger.measurement.source))) {
      // Persist invalidation so selecting the previous route/budget again does
      // not resurrect a measurement from before the intervening transition.
      delete ledger.measurement; save(user,ledger);
    }
    const proof = ledger.measurement?.modelRevision === model.revision && ledger.measurement.sessionRevision === sessionRevision
      ? ledger.measurement : null;
    // OpenClaw can mark cumulative session usage fresh after tool continuations.
    // It is not the occupancy of one model call and must not replace its proof.
    const nativeFresh = !requireModelObservation && entry?.totalTokensFresh === true && (!proof || entry.updatedAt >= proof.measuredAt);
    // Session metadata is saved after llm_output and may have a newer timestamp.
    // That alone does not invalidate this model's measured usage. A subsequent
    // compaction does, unless it supplied its own post-compaction measurement.
    const observedFresh = proof && (integer(entry?.compactionCount) ?? 0) <= (integer(proof.compactionCount) ?? 0);
    const reportedWindow = proof?.window || window;
    const measuredUsed = !requireModelObservation || proof ? nativeFresh ? integer(entry.totalTokens) : observedFresh ? proof.used : null : null;
    // A single model call cannot occupy more than its own effective context
    // budget. OpenClaw may report cumulative session usage after a continuation;
    // keep that impossible value out of the context meter and manual receipt.
    const used = measuredUsed !== null && reportedWindow && measuredUsed <= reportedWindow ? measuredUsed : null;
    const at = nativeFresh ? entry?.updatedAt : proof?.measuredAt;
    const measuredAt = Number.isSafeInteger(at) && at > 0 && at <= 8640000000000000 ? new Date(at).toISOString() : null;
    const count = integer(entry?.compactionCount) ?? 0;
    const operation = preferred ?? ledger.operations.at(-1);
    const compaction = operation ? {status:operation.status,requestId:operation.requestId,
      tokensBefore:operation.tokensBefore,tokensAfter:operation.tokensAfter,reason:operation.reason,count} : idle(count);
    const status = admission.status();
    const uncertain = operation?.status === 'unknown' && admission.owns(operation.leaseToken);
    return {schemaVersion:1, status:uncertain || status?.available !== true ? 'unavailable'
      : pending.has(user) || activeSession(keyFor(user)) ? 'busy' : sessionRevision ? 'ready' : 'missing',
      sessionExists:sessionRevision !== null, sessionRevision,
      context:used !== null && measuredAt ? {used,window:reportedWindow,measuredAt} : null,
      model:id && provider && window && provider !== 'ods-policy' ? {id,provider,contextWindow:proof?.window || window,
        ...(model.routeFingerprint ? {routeFingerprint:model.routeFingerprint} : {})} : null, compaction};
  }
  function unavailable(user) {
    return {schemaVersion:1,status:'unavailable',sessionExists:false,sessionRevision:null,context:null,model:null,compaction:idle(0)};
  }
  function context(user) {
    keyFor(user);
    try { const ledger = read(user); recover(user, ledger); return project(user, entryFor(user), ledger); }
    catch { return unavailable(user); }
  }
  async function execute(user, ledger, item, entry) {
    const sessionKey = keyFor(user); let modelLease, timer, dispatched = false;
    try {
      modelLease = await prepareModel({sessionKey,sessionId:entry.sessionId,entry,leaseToken:item.leaseToken});
      // The admission lease predates this RPC and remains held until its exact
      // final response. sessions.compact itself must never find an active run.
      if (!admission.owns(item.leaseToken) || activeSession(sessionKey) ||
          revisionFor(user, entryFor(user)) !== item.sessionRevision) throw ERROR();
      dispatched = true;
      timer = setTimeout(() => {
        item.status = 'unknown'; item.reason = 'result-unconfirmed';
        try { save(user,ledger); } catch { /* Preserve the held barrier. */ }
      },timeoutMs);
      timer.unref?.();
      // Continue observing the original promise after a local deadline. A
      // later authenticated final response can settle this exact operation;
      // a disconnected RPC remains unknown and is never dispatched twice.
      const result = await callGateway('sessions.compact', {timeoutMs}, {key:sessionKey,agentId});
      clearTimeout(timer);
      if (result?.key !== sessionKey || typeof result.ok !== 'boolean' || typeof result.compacted !== 'boolean') throw ERROR();
      item.status = result.ok ? result.compacted ? 'completed' : 'skipped' : 'failed';
      item.reason = result.ok ? result.compacted ? null : result.reason === 'no transcript' || result.reason === 'no sessionId'
        ? 'missing-transcript' : 'not-needed' : 'runtime-failed';
      item.tokensBefore = integer(result.result?.tokensBefore) ?? item.tokensBefore;
      item.tokensAfter = integer(result.result?.tokensAfter);
      if (item.status === 'completed' && item.tokensAfter !== null) {
        const latest=entryFor(user), model=modelFor(readConfig(),latest,agentId), revision=revisionFor(user,latest);
        if (model.window && revision) ledger.measurement={modelRevision:model.revision,sessionRevision:revision,
          source:'native-compaction',used:item.tokensAfter,window:model.window,measuredAt:now(),compactionCount:integer(latest?.compactionCount) ?? 0};
      }
      await modelLease.close(); modelLease = undefined;
      save(user, ledger);
      admission.release(item.leaseToken);
    } catch (error) {
      // A rejected/expired transport does not prove the RPC stopped. Keep the
      // barrier and receipt unknown; a repeated request never starts it again.
      const refused = error?.name === 'GatewayClientRequestError' && error.gatewayCode === 'UNAVAILABLE' &&
        error.message === 'Session is active; retry compaction after the current run finishes.';
      item.status = dispatched && !refused ? 'unknown' : 'failed';
      item.reason = refused ? 'runtime-busy' : dispatched ? 'result-unconfirmed' : error?.code === 'unsupported-model' ? 'unsupported-model' : 'coordination-failed';
      try {
        if (!dispatched || refused) await modelLease?.close();
        save(user, ledger);
        if (!dispatched || refused) admission.release(item.leaseToken);
      } catch { /* Held admission remains conservative. */ }
    } finally { clearTimeout(timer); pending.delete(user); }
  }
  async function compact(user, requestId) {
    keyFor(user); if (!REQUEST.test(requestId ?? '')) throw ERROR();
    let ledger, token, acquired = false;
    try {
      ledger = read(user); recover(user, ledger);
      const previous = ledger.operations.find(item => item.requestId === requestId);
      if (previous) return project(user, entryFor(user), ledger, previous);
      const entry = entryFor(user), snapshot = project(user, entry, ledger);
      if (admission.status()?.available === true && admission.status().phase !== 'idle') return {...snapshot,status:'busy'};
      if (snapshot.status !== 'ready') return snapshot;
      // A full journal is a confirmed pre-admission refusal, not an unknown
      // RPC outcome. Return this request's receipt so callers can release their
      // own transition fence, without evicting old idempotency records.
      if (ledger.operations.length >= maximumRequests) return {...snapshot,status:'unavailable',
        compaction:{...idle(snapshot.compaction.count),status:'failed',requestId,reason:'request-limit'}};
      token = crypto.randomBytes(32).toString('hex');
      const admissionStatus = admission.status();
      if (admissionStatus.phase !== 'idle' || activeSession(keyFor(user))) return {...snapshot,status:'busy'};
      await admission.acquire(token, admissionStatus.revision); acquired = true;
      if (activeSession(keyFor(user)) || revisionFor(user, entryFor(user)) !== snapshot.sessionRevision) {
        admission.release(token); return context(user);
      }
      const item = {requestId,status:'running',tokensBefore:snapshot.context?.used ?? null,tokensAfter:null,reason:null,
        leaseToken:token,instance:instanceId,sessionRevision:snapshot.sessionRevision};
      ledger.operations.push(item); save(user, ledger);
      pending.set(user, item);
      queueMicrotask(() => { void execute(user, ledger, item, entry); });
      return project(user, entry, ledger);
    } catch {
      if (acquired && !pending.has(user)) {
        try { admission.release(token); } catch { /* Never claim release if proof failed. */ }
      }
      const snapshot=context(user);
      return admission.status()?.available === true && admission.status().phase !== 'idle' ? {...snapshot,status:'busy'} : snapshot;
    }
  }
  async function withMaintenance(user, callback) {
    const sessionKey = keyFor(user), ledger = read(user);
    recover(user,ledger);
    const snapshot = admission.status(), before = entryFor(user);
    if (snapshot?.available !== true || snapshot.phase !== 'idle' || activeSession(sessionKey) || pending.size) throw ERROR();
    const token = crypto.randomBytes(32).toString('hex');
    await admission.acquire(token, snapshot.revision);
    try {
      if (!admission.owns(token) || activeSession(sessionKey) || revisionFor(user, before) !== revisionFor(user, entryFor(user))) throw ERROR();
      ledger.maintenance = {leaseToken:token,instance:instanceId}; save(user,ledger);
      return await callback({sessionKey,session:before ?? null,readSession:() => entryFor(user) ?? null});
    } finally { admission.release(token); delete ledger.maintenance; save(user,ledger); }
  }
  function observeModelInput(event, context) {
    const prefix=`agent:${agentId}:openai-user:`, key=context?.sessionKey, runId=text(context?.runId ?? event?.runId);
    if(context?.agentId!==agentId || !key?.startsWith(prefix) || !USER.test(key.slice(prefix.length)) || !runId) return;
    try {
      const user=key.slice(prefix.length), entry=entryFor(user), model=modelFor(readConfig(),entry,agentId);
      modelAttempts.delete(key);
      modelAttempts.set(key,{runId,startedAt:now(),modelRevision:model.revision,sessionId:context.sessionId});
      if(modelAttempts.size>128) modelAttempts.delete(modelAttempts.keys().next().value);
    } catch { /* Missing identity cannot establish usage evidence. */ }
  }
  function observeModelOutput(event, context) {
    const prefix=`agent:${agentId}:openai-user:`, key=context?.sessionKey;
    if(context?.agentId!==agentId || typeof key!=='string' || !key.startsWith(prefix)) return;
    const user=key.slice(prefix.length), usage=event?.lastAssistant?.usage, window=integer(event?.contextTokenBudget);
    if(!USER.test(user) || !window || !usage) return;
    const attempt=modelAttempts.get(key), assistant=event.lastAssistant;
    // An overflow precheck can emit llm_output with the previous turn's final
    // assistant, including cumulative usage. Only this attempt's model reply
    // is evidence of current occupancy; never relabel history after a switch.
    if(!attempt || attempt.runId!==(context?.runId ?? event?.runId) || attempt.sessionId!==context.sessionId ||
        !Number.isSafeInteger(assistant.timestamp) || assistant.timestamp<attempt.startedAt ||
        ['error','aborted'].includes(assistant.stopReason)) return;
    const counts=['input','output','cacheRead','cacheWrite'].map(field=>usage[field]===undefined?0:integer(usage[field]));
    if(counts.some(value=>value===null)) return;
    const used=integer(counts.reduce((a,b)=>a+b,0));if(!used) return;
    if(usage.totalTokens!==undefined && integer(usage.totalTokens)!==used) return;
    try {
      const entry=entryFor(user), model=modelFor(readConfig(),entry,agentId), revision=revisionFor(user,{sessionId:context.sessionId});
      if(!revision || model.revision!==attempt.modelRevision || model.window && window>model.window) return;
      const ledger=read(user);
      if(used>window) {
        // This reply belongs to the current run, but its usage cannot be a
        // single-window occupancy. Retire the prior proof rather than showing
        // an old percentage while the current call is unmeasured.
        if(ledger.measurement) {delete ledger.measurement;save(user,ledger);}
        return;
      }
      ledger.measurement={source:'model-call-v2',modelRevision:model.revision,sessionRevision:revision,used,window,measuredAt:now(),compactionCount:integer(entry?.compactionCount) ?? 0};
      save(user,ledger);
    } catch { /* Unknown metadata stays unknown; never break the agent reply. */ }
  }
  function recoverOwnedAtStartup() {
    // The barrier is process-wide. Recover its exact journal even when the
    // first browser request after restart opens a different conversation.
    // Normal startups do not enumerate anything; an unknown/external hold is
    // never released merely because no matching record was found.
    if (admission.status()?.phase !== 'held' || !fs.existsSync(directory)) return;
    let entries;
    try {
      check(directory,true);entries=fs.opendirSync(directory);
      for(let count=0;count<4096;count++) {
        const item=entries.readSync();if(!item)break;
        const match=/^(ods-[a-f0-9]{64})\.json$/.exec(item.name);
        if(!item.isFile() || !match)continue;
        try {
          const ledger=read(match[1]), candidates=[...ledger.operations,ledger.maintenance].filter(Boolean);
          if(candidates.some(record=>record.instance!==instanceId && admission.owns(record.leaseToken))) {
            recover(match[1],ledger);
            if(admission.status()?.phase!=='held')break;
          }
        } catch { /* Unreadable custody is not authority to clear a hold. */ }
      }
    } catch { /* Preserve unknown/external admission state. */ }
    finally { entries?.closeSync(); }
  }
  recoverOwnedAtStartup();
  return {context, compact, withMaintenance, observeModelInput, observeModelOutput};
}
