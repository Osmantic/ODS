import {createHash} from 'node:crypto';
import {integrationRequest, snapshotIntegrationRequest, reviewIntegrationCandidate, canonicalReviewNotePath,
  canonicalReviewDocumentId, INTEGRATION_TOOL_NAMES} from './integrations.mjs';

// Native outcome evidence and refusal after uncertainty. Never executes,
// reads approvals, parses model prose or requests another model attempt.
const PENDING = 'This call did not run the action; it requested approval. Review Settings → Portal integrations, then ask Portal to retry the same action.';
const UNKNOWN = 'Its result could not be verified; check the destination before retrying.';
const READY = 'This exact action already has owner approval, but this review-only recovery did not run it or use the approval. Send a new request to execute it.';
export const MAX_INTEGRATION_NOTICE = 2048;
const identity = value => typeof value === 'string' && value.length > 0 && value.length <= 512 && !/[\x00-\x20\x7f]/.test(value);
const names = new Set(INTEGRATION_TOOL_NAMES);
const actions = new Set(['notes-write', 'notify', 'mcp-call']);
const reviewReads = new Set(['notes-search', 'notes-read', 'mcp-tools']);
const tracked = (payload, reviewOnly) => actions.has(payload?.action) || reviewOnly && reviewReads.has(payload?.action);
const SAFE_APP_READS = Object.freeze({'app:documents':['search','read'], 'app:photos':['search'],
  'app:watch':['list'], 'app:news':['unread','digest']});
const UNRESOLVED = 'An earlier integration call in this response has an unverified result. This additional action was not submitted. Check the destination, then send a new request before trying again. Known built-in reads and the catalog remain available.';
const effectful = payload => payload?.action === 'notify' || payload?.action === 'notes-write' ||
  payload?.action === 'mcp-call' && !(Object.hasOwn(SAFE_APP_READS,payload.server) && SAFE_APP_READS[payload.server].includes(payload.tool));
const childOf = (id,call) => {
  if (typeof id!=='string') return false;
  const parent = call.id.replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0,120) || 'call';
  const prefix = `tool_search_code:${parent}:${call.name}:`;
  return call.deferred && id.startsWith(prefix) && /^[1-9][0-9]*$/.test(id.slice(prefix.length));
};
const REVIEW_REFUSED = 'Portal did not submit this integration candidate because its current restrictions or reference could not be verified. No action or approval request was created by this candidate. Check the saved results, then send a new request.';
const REVIEW_SERVICE_REFUSED = 'The service refused this proposed integration call because its reference was invalid or no longer current. No change or approval request was created by this call. Automatic review stopped; check the saved results, then send a new request.';
const REVIEW_LOOKUP_INVALID = 'The service returned an invalid or missing lookup reference. This lookup was submitted, but its result cannot ground another integration call. Automatic review stopped.';
const referenceSources = new Set(['obsidian','paperless','watch']);
const hexBinding = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const exactKeys = (value,keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
function freezeJson(value) {
  if (value && typeof value === 'object') {for (const child of Object.values(value)) freezeJson(child);Object.freeze(value);}
  return value;
}
function referenceSource(payload) {
  if (payload?.action?.startsWith('notes-')) return 'obsidian';
  if (payload?.action==='mcp-call') return {'app:documents':'paperless','app:watch':'watch'}[payload.server];
}
function referenceId(source,entry) {return source==='obsidian'?entry.path:entry.id;}
function referenceTarget(payload,source) {
  if (source==='obsidian' && ['notes-read','notes-write'].includes(payload.action)) return canonicalReviewNotePath(payload.path);
  if (source==='paperless' && payload.tool==='read') return canonicalReviewDocumentId(payload.arguments?.id);
  if (source==='watch' && payload.tool==='remove') return payload.arguments?.id;
}
const contentRead=payload=>payload?.action==='notes-read'||payload?.action==='mcp-call'
  &&payload.server==='app:documents'&&payload.tool==='read';
function readReferences(value,source) {
  const copy=snapshotIntegrationRequest(value);
  if (!exactKeys(copy,['version','source','binding','entries','truncated']) || copy.version!==1 || copy.source!==source
      || !referenceSources.has(source) || !hexBinding(copy.binding) || typeof copy.truncated!=='boolean'
      || !Array.isArray(copy.entries) || copy.entries.length>16 || Buffer.byteLength(JSON.stringify(copy))>16384) throw new Error('invalid-review-reference');
  const ids=new Set();
  for (const entry of copy.entries) {
    const valid = source==='obsidian' ? exactKeys(entry,['path','binding']) && canonicalReviewNotePath(entry.path)===entry.path && hexBinding(entry.binding)
      : source==='paperless' ? exactKeys(entry,['id','title']) && canonicalReviewDocumentId(entry.id)===entry.id && typeof entry.title==='string' && [...entry.title].length<=200
        : exactKeys(entry,['id','title','url']) && typeof entry.id==='string' && entry.id.length>0 && [...entry.id].length<=128
          && !/[\x00-\x1f\x7f]/.test(entry.id) && typeof entry.title==='string' && [...entry.title].length<=200
          && typeof entry.url==='string' && [...entry.url].length<=500 && (()=>{try{const url=new URL(entry.url);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password;}catch{return false;}})();
    const id=referenceId(source,entry);
    if (!valid || ids.has(id)) throw new Error('invalid-review-reference');ids.add(id);
  }
  return freezeJson(copy);
}

function requestKey(payload) {
  const json = JSON.stringify(snapshotIntegrationRequest(payload));
  return createHash('sha256').update(json).digest('hex');
}

const effectiveKey = (baseKey, reviewOnly) => reviewOnly
  ? createHash('sha256').update(JSON.stringify({request:baseKey,recoveryReviewOnly:true})).digest('hex') : baseKey;

function label(payload) {
  if (payload.action === 'notify') return 'Notification';
  if (payload.action === 'notes-write') return payload.mode === 'create' ? 'Create an Obsidian note' : 'Add to an Obsidian note';
  if (payload.action === 'notes-search') return 'Search Obsidian notes';
  if (payload.action === 'notes-read') return 'Read an Obsidian note';
  if (payload.action === 'mcp-tools') return 'Integration catalog';
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
  const runs = new Map(), bySession = new Map(), byKey = new Map(), reservations = new WeakMap(), candidates = new WeakMap();
  let generation = 0;
  const owned = run => run && runs.get(run.id) === run && !run.invalid &&
    bySession.get(run.sessionId) === run.id && (!run.sessionKey || byKey.get(run.sessionKey) === run.id);
  const current = run => owned(run) && !run.cancelled;
  const reviewCurrent = call => current(call?.run) && !call.run.ended && !call.run.reviewStopped && !call.closed
    && !(call.run.uncertain && effectful(call.payload));
  function pump(run) {
    if (run.reviewLease) return;
    while (run.reviewQueue?.length) {
      const next=run.reviewQueue.shift();
      if (!reviewCurrent(next.call)) {next.resolve(false);continue;}
      run.reviewLease=next.call;next.call.lease=true;next.resolve(true);return;
    }
  }
  function release(call) {
    if (!call?.candidate || call.released) return;
    call.released=true;
    if (call.run.reviewLease===call) call.run.reviewLease=null;
    pump(call.run);
  }
  function drain(run,terminal=false,notice=REVIEW_REFUSED) {
    if (!run) return;
    if (terminal) {run.reviewStopped=true;run.reviewNotice=notice;}
    for (const call of run.calls) if (call.candidate && (!call.taken || terminal)) {
      call.closed=true;call.pending=false;release(call);
    }
    for (const waiting of run.reviewQueue?.splice(0) ?? []) waiting.resolve(false);
  }
  function candidateBlocked(call,code='review-candidate-refused',terminal=true) {
    call.closed=true;call.pending=false;
    if (terminal) drain(call.run,true);else release(call);
    return {block:true,blockReason:code==='integration-outcome-unresolved'?UNRESOLVED:REVIEW_REFUSED,code};
  }
  function publicReferences(run,source) {
    const groups=(run.referenceLookups??[]).filter(group=>group.envelope.source===source);
    return freezeJson({lookups:groups.map(group=>({source,query:group.query,
      entries:group.envelope.entries.map(({binding,...entry})=>entry),truncated:group.envelope.truncated})),
      truncated:Boolean(run.referencesTruncated || groups.some(group=>group.envelope.truncated))});
  }
  function completedReads(run,before=Infinity) {
    return run.calls.filter(call=>call.sequence<before&&call.status==='settled'&&call.readCompleted&&!call.readInvalidated
      &&run.referenceBindings.get(call.readCompleted.source)===call.readCompleted.binding);
  }
  function publicProgress(run,payload,before) {
    const facts=completedReads(run,before),source=referenceSource(payload),target=referenceTarget(payload,source);
    const matching=facts.findLast(call=>call.readCompleted.source===source&&call.readCompleted.target===target);
    const ordered=[...(matching?[matching]:[]),...facts.toReversed().filter(call=>call!==matching)];
    const reads=[];let omitted=false;
    for(const call of ordered) {
      const fact={sequence:call.sequence,raw:snapshotIntegrationRequest(call.raw),status:'read-complete'};
      if(reads.length===16||Buffer.byteLength(JSON.stringify({reads:[...reads,fact],truncated:true}))>8192) {omitted=true;continue;}
      reads.push(fact);
    }
    return freezeJson({reads,truncated:Boolean(run.progressTruncated||omitted)});
  }
  function referenceContext(run,payload) {
    const source=referenceSource(payload),groups=(run.referenceLookups??[]).filter(group=>group.envelope.source===source);
    // Public facts can be clipped, but clipping must never erase a previously
    // observed source epoch. An empty envelope still makes the host compare the
    // actual connection/vault before this call accesses private data.
    const binding=run.referenceBindings?.get(source);
    if (!binding) return undefined;
    if (groups.some(group=>group.envelope.binding!==binding)) throw new Error('stale-review-reference');
    const target=referenceTarget(payload,source),all=[];
    for (const group of [...groups].reverse()) for (const entry of [...group.envelope.entries].reverse())
      if (!all.some(value=>referenceId(source,value)===referenceId(source,entry))) all.push(entry);
    // A bounded progress fact may outlive clipping of public lookup entries.
    // Its own exact read target remains the expected identity at dispatch.
    const read=completedReads(run).findLast(call=>call.readCompleted.source===source&&call.readCompleted.target===target);
    if(read&&!all.some(entry=>referenceId(source,entry)===target))all.unshift(read.readCompleted.entry);
    const match=target===undefined?undefined:all.find(entry=>referenceId(source,entry)===target);
    // A retained matching target always wins the bounded subset, including a
    // normalized note alias and decimal-string document IDs beyond JS precision.
    const selected=[...(match?[match]:[]),...all.filter(entry=>entry!==match)].slice(0,16);
    if (match && !selected.includes(match)) throw new Error('invalid-review-reference');
    return readReferences({version:1,source,binding,entries:selected,
      truncated:Boolean(run.referencesTruncated || groups.some(group=>group.envelope.truncated) || selected.length<all.length)},source);
  }
  function retainReferences(call,value,completion) {
    const source=referenceSource(call.payload);
    const lookup=call.payload.action==='notes-search' || call.payload.action==='mcp-call' &&
      (call.payload.server==='app:documents'&&call.payload.tool==='search' || call.payload.server==='app:watch'&&call.payload.tool==='list');
    const directRead=contentRead(call.payload);
    if (!lookup&&!directRead) return true;
    if (directRead&&completion?.readCompleted!==true) return false;
    if (!source || !current(call.run) || call.run.ended || call.run.reviewStopped) return false;
    let envelope;try{envelope=readReferences(value,source);}catch{return false;}
    const run=call.run;
    const target=referenceTarget(call.payload,source),readEntry=directRead?envelope.entries.find(entry=>referenceId(source,entry)===target):undefined;
    if(directRead&&!readEntry&&(!envelope.truncated||envelope.entries.length))return false;
    for(const previous of run.calls)if(previous.readCompleted&&!previous.readInvalidated&&previous.readCompleted.source===source) {
      const changed=envelope.entries.find(entry=>referenceId(source,entry)===previous.readCompleted.target);
      if(previous.readCompleted.binding!==envelope.binding||changed&&requestKey(changed)!==requestKey(previous.readCompleted.entry)) {
        previous.readInvalidated=true;run.progressTruncated=true;
      }
    }
    if(directRead) {
      if(readEntry)call.readCompleted=freezeJson({source,binding:envelope.binding,target,entry:readEntry});
      else run.progressTruncated=true; // Successful bounded read, no retainable target proof.
    }
    run.referenceBindings.set(source,envelope.binding);
    let groups=run.referenceLookups??[];
    if (groups.some(group=>group.envelope.source===source && group.envelope.binding!==envelope.binding)) groups=groups.filter(group=>group.envelope.source!==source);
    const replaced=new Set(envelope.entries.map(entry=>referenceId(source,entry)));
    groups=groups.map(group=>group.envelope.source!==source?group:{...group,envelope:{...group.envelope,entries:group.envelope.entries.filter(entry=>!replaced.has(referenceId(source,entry)))}}).filter(group=>group.envelope.entries.length);
    const query=call.payload.action==='notes-search'?call.payload.query:typeof call.payload.arguments?.query==='string'?call.payload.arguments.query:null;
    groups.push({envelope,query,requestDigest:call.baseKey,sequence:call.sequence});
    const count=()=>groups.reduce((total,group)=>total+group.envelope.entries.length,0);
    while (groups.length && (count()>64 || Buffer.byteLength(JSON.stringify(groups))>16384)) {
      const first=groups[0];groups[0]={...first,envelope:{...first.envelope,entries:first.envelope.entries.slice(1),truncated:true}};
      if (!groups[0].envelope.entries.length) groups.shift();run.referencesTruncated=true;
    }
    run.referenceLookups=freezeJson(groups);
    return true;
  }
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
      old.cancelled=true;drain(old);
      if (bySession.get(old.sessionId) === id) bySession.delete(old.sessionId);
      if (byKey.get(old.sessionKey) === id) byKey.delete(old.sessionKey);
    }
    run = {id:context.runId, sessionId:context.sessionId, sessionKey:context.sessionKey, calls:[], sequence:0,
      generation:++generation, uncertain,reviewQueue:[],referenceLookups:[],referenceBindings:new Map()};
    runs.set(run.id, run);
    bySession.set(run.sessionId, run.id);
    if (run.sessionKey) byKey.set(run.sessionKey, run.id);
    if (prior && prior!==run) drain(prior);
  }
  function reviewStartClear(context) {
    // A query cannot adopt a sparse context, rebind an owner, reopen a run or
    // clear uncertainty. In particular, do not call the mutating bind().
    if (context?.agentId !== 'pixel' || ![context.runId,context.sessionId,context.sessionKey].every(identity)) return false;
    const run = runs.get(context.runId);
    return Boolean(current(run) && !run.ended && !run.reviewStopped && !run.uncertain && !run.overflow
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
    // A latched review can only enter via provisional/check/commit below.
    // This legacy synchronous observer must never mint an unchecked ticket.
    if (run.reviewOnly) return {block:true,blockReason:REVIEW_REFUSED};
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
      if (!tracked(payload,run.reviewOnly === true)) return;
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
  function eventCandidate(event,context,decision) {
    const params=decision?.params??event?.params;
    const deferred=event?.toolName==='tool_call';
    let name=event?.toolName,raw=params;
    if (deferred) {
      if (typeof params?.id!=='string' || !params.id.startsWith('openclaw:pixel-ods:')) throw new Error('invalid-review-arguments');
      name=params.id.slice('openclaw:pixel-ods:'.length);raw=params.args;
    }
    const candidate=reviewIntegrationCandidate(name,raw,context);
    return {...candidate,name,deferred,rawKey:requestKey(candidate.raw),baseKey:requestKey(candidate.payload)};
  }
  function unchanged(call) {
    try{return call.sources.every(source=>{
      if (!sameReviewContext(call.run,source.context)) return false;
      const next=eventCandidate(source.event,source.context,source.decision);
      return next.name===call.name && next.rawKey===call.rawKey && next.baseKey===call.baseKey;
    });}catch{return false;}
  }
  const sameReviewContext=(run,context)=>context?.agentId==='pixel' && context.runId===run.id
    && context.sessionId===run.sessionId && context.sessionKey===run.sessionKey;
  async function prepareReviewCandidate(event,context,decision) {
    if (decision?.block) return decision;
    const run=runs.get(context?.runId),id=context?.toolCallId??event?.toolCallId;
    if (!run || !sameReviewContext(run,context) || !current(run) || run.ended || !identity(id)) return {block:true,blockReason:REVIEW_REFUSED};
    run.reviewOnly=true; // Monotonic restriction, not a ticket or action status.
    if (run.reviewStopped || run.overflow) return {block:true,blockReason:REVIEW_REFUSED};
    let input;try{input=eventCandidate(event,context,decision);}catch{drain(run,true);return {block:true,blockReason:REVIEW_REFUSED};}
    if (run.uncertain && effectful(input.payload)) return {block:true,blockReason:UNRESOLVED,code:'integration-outcome-unresolved'};
    let call=run.calls.find(value=>value.id===id || value.nativeChild===id);
    if (!call && !input.deferred) {
      const parents=run.calls.filter(value=>value.candidate && value.name===input.name && childOf(id,value));
      if (parents.length===1) {
        call=parents[0];
        if (call.nativeChild && call.nativeChild!==id) return candidateBlocked(call);
        call.nativeChild=id;
      } else if (parents.length>1) {drain(run,true);return {block:true,blockReason:REVIEW_REFUSED};}
    }
    if (call) {
      if (!call.candidate || call.taken || call.closed || call.name!==input.name || call.baseKey!==input.baseKey || call.rawKey!==input.rawKey)
        return candidateBlocked(call);
      if (!call.sources.some(source=>source.event===event)) call.sources.push({event,context,decision});
      return call.prepared;
    }
    if (run.calls.length>=maximumCalls) {run.overflow=true;drain(run,true);return {block:true,blockReason:REVIEW_REFUSED};}
    call={id,name:input.name,deferred:input.deferred,rawKey:input.rawKey,baseKey:input.baseKey,
      key:effectiveKey(input.baseKey,true),payload:freezeJson(input.payload),raw:freezeJson(input.raw),maximumEffect:input.maximumEffect,
      recoveryReviewOnly:true,label:label(input.payload),sequence:++run.sequence,run,pending:true,taken:false,status:null,
      candidate:true,sources:[{event,context,decision}],committed:false,released:false};
    run.calls.push(call);
    call.prepared=(async()=>{
      const leased=await new Promise(resolve=>{run.reviewQueue.push({call,resolve});pump(run);});
      if (run.uncertain && effectful(call.payload)) return candidateBlocked(call,'integration-outcome-unresolved',false);
      if (!leased || !reviewCurrent(call) || !unchanged(call)) return candidateBlocked(call,'review-candidate-refused',current(run)&&!run.ended);
      let expected;try{expected=referenceContext(run,call.payload);}catch{return candidateBlocked(call);}
      call.referenceContext=expected;
      const ref=Object.freeze({});
      const view=freezeJson({raw:snapshotIntegrationRequest(call.raw),payload:snapshotIntegrationRequest(call.payload),
        maximumEffect:call.maximumEffect,references:publicReferences(run,referenceSource(call.payload)),
        progress:publicProgress(run,call.payload,call.sequence)});
      const candidate=Object.freeze({ref,view});candidates.set(ref,call);call.preparedValue=candidate;return candidate;
    })();
    return call.prepared;
  }
  async function checkReviewCandidate(candidate,check) {
    const call=candidate?.ref&&candidates.get(candidate.ref);
    if (!call || call.preparedValue!==candidate) return undefined;
    if (call.checkPromise) return call.checkPromise;
    call.checkPromise=(async()=>{
      if (!reviewCurrent(call) || !unchanged(call)) return undefined;
      let verdict;
      try{verdict=call.maximumEffect==='catalog-metadata'?Object.freeze({candidate:candidate.ref,entailed:true,code:'review-metadata'}):await check(candidate);}
      catch{verdict=Object.freeze({candidate:candidate.ref,entailed:false,code:'review-check-failed'});}
      if (!reviewCurrent(call) || !unchanged(call)) return undefined;
      call.verdict=verdict;return verdict;
    })();
    return call.checkPromise;
  }
  function commitReviewCandidate(candidate,verdict) {
    const call=candidate?.ref&&candidates.get(candidate.ref);
    if (!call || call.preparedValue!==candidate) return {block:true,blockReason:REVIEW_REFUSED};
    if (call.run.uncertain && effectful(call.payload)) return candidateBlocked(call,'integration-outcome-unresolved',false);
    if (!reviewCurrent(call) || !unchanged(call)) return candidateBlocked(call);
    if (call.committed) return undefined; // Exact native wrapper/child reuse.
    if (call.verdict!==verdict || !Object.isFrozen(verdict) || verdict?.candidate!==candidate.ref || verdict.entailed!==true)
      return candidateBlocked(call);
    call.committed=true;return undefined;
  }
  function refuseReviewCall(id,context,code='review-candidate-refused',trustedControl) {
    const trusted=trustedControl?.recoveryReviewOnly===true;
    const run=context?.runId?runs.get(context.runId):trusted?undefined:runs.get(bySession.get(context?.sessionId));
    // A first native floor refusal precedes candidate preparation. Record only
    // the restrictive fact on the EXACT existing owner; never adopt a sparse
    // context, produce a candidate or grant a factory ticket here.
    if (trusted) {
      if (!run || !sameReviewContext(run,context) || !current(run) || run.ended) return;
      run.reviewOnly=true;
    }
    if (!run || !run.reviewOnly || !bind(run,context,true)) return;
    const call=run.calls.find(value=>value.id===id || value.nativeChild===id || childOf(id,value));
    const terminal=code!=='integration-outcome-unresolved';
    if (call) candidateBlocked(call,code,terminal);else if(terminal)drain(run,true);
  }
  function take(id, payload, context, raw) {
    if (!identity(id) || !tracked(payload,true)) return undefined;
    let key;
    try {key = requestKey(payload);} catch {return undefined;}
    const candidates = [];
    for (const run of runs.values()) {
      if (!bind(run, context) || run.ended || !run.sessionKey || !tracked(payload,run.reviewOnly === true)) continue;
      for (const call of run.calls) {
        if (!call.pending || call.taken || call.baseKey !== key) continue;
        if (run.reviewOnly) {
          if (!call.candidate || !call.committed || !reviewCurrent(call) || !unchanged(call) || run.reviewLease!==call) continue;
          try{const validated=reviewIntegrationCandidate(raw?.name,raw?.params,context);
            if(requestKey(validated.raw)!==call.rawKey || requestKey(validated.payload)!==call.baseKey)continue;
          }catch{continue;}
        }
        if (call.nativeChild ? call.nativeChild === id : call.id === id || childOf(id,call)) candidates.push(call);
      }
    }
    if (candidates.length !== 1) return undefined;
    const call = candidates[0], token = Object.freeze({});
    call.taken = true;
    reservations.set(token, call);
    return token;
  }
  function complete(token, status, references, code, completion) {
    const call = token && reservations.get(token);
    if (!call) return;
    reservations.delete(token);
    try {
      if (!current(call.run) || call.closed || call.run.ended || !['pending', 'unknown', 'settled', 'ready','refused'].includes(status)) return;
      call.status = status;
      if (status === 'unknown') call.run.uncertain = true;
      if (call.recoveryReviewOnly && status==='settled' && !retainReferences(call,references,completion)) {
        call.status='unknown';call.run.uncertain=true;drain(call.run,true,REVIEW_LOOKUP_INVALID);return false;
      }
      if (call.recoveryReviewOnly && ['invalid-review-reference','stale-review-reference'].includes(code)) drain(call.run,true,REVIEW_SERVICE_REFUSED);
      else if (call.recoveryReviewOnly && code==='integration-call-unbound') drain(call.run,true);
      return true;
    } finally {release(call);}
  }
  function finish(token) {
    const call=token&&reservations.get(token);if(!call)return;
    reservations.delete(token);if(call.status===null)call.status='refused';release(call);
  }
  function control(token) {
    const call = token && reservations.get(token);
    if (!call || !current(call.run) || call.run.ended || call.closed
      || call.recoveryReviewOnly && (!reviewCurrent(call) || !unchanged(call))) return undefined;
    return call.recoveryReviewOnly ? {recoveryReviewOnly:true,...(call.referenceContext?{recoveryReferenceContext:call.referenceContext}:{})} : {};
  }
  function after(event, context) {
    const run = runs.get(context?.runId);
    if (!run || !bind(run, context)) return;
    const id = context?.toolCallId ?? event?.toolCallId;
    const call = run.calls.find(item => item.id === id || item.nativeChild === id);
    if (call) {
      call.pending = false;
      // A later native guard may return a result without invoking the factory.
      // That family has settled with no host submission, so it must not strand
      // unrelated queued reads. An already submitted host keeps its lease until
      // the direct receipt settles it.
      if (call.candidate && !call.taken) {call.closed=true;release(call);}
    }
  }
  function end(event, context) {
    const run = runs.get(context?.runId ?? event?.runId);
    if (!run || !bind(run, context)) return;
    run.ended = true;
    for (const call of run.calls) if (call.status === null) {call.closed = true; call.pending = false;}
    if (event?.success === false) run.cancelled = true;
    drain(run);
  }
  function cancelUser(user) {
    if (typeof user !== 'string') return;
    for (const run of runs.values()) if (run.sessionKey === `agent:pixel:openai-user:${user}`) {run.cancelled = true;drain(run);}
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
    if (!visible.length && !run.overflow && !run.reviewNotice) return undefined;
    const lines = visible.slice(0, 6).map(call => `${call.label} (request ${call.sequence}): ${call.status === 'pending' ? PENDING : call.status === 'ready' ? READY : UNKNOWN}`);
    if (visible.length > 6 || run.overflow) lines.push('Additional integration call statuses are not shown in this notice.');
    if (run.reviewNotice) lines.push(run.reviewNotice);
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
  return {begin, reviewStartClear, before, prepareReviewCandidate,checkReviewCandidate,commitReviewCandidate,refuseReviewCall,
    take, control, complete,finish, after, end, cancelUser, projection, verification, channel, refusal, effectful,
    clear() {for(const run of runs.values()){run.cancelled=true;drain(run);}runs.clear(); bySession.clear(); byKey.clear();}};
}
