// One optional native continuation. The model keeps planning in its SAME
// session; this module never classifies intent or asks a second model to judge
// a candidate. Admission proves native custody/role, not owner entailment.
import {snapshotIntegrationRequest,reviewIntegrationCandidate,NATIVE_REVIEW_ROLE,nativeReviewOperation} from './integrations.mjs';

const integrations=new Set(['pixel_ods_notes','pixel_ods_mcp']);
const canonicalIntegrations=new Set([...integrations].map(name=>`openclaw:pixel-ods:${name}`));
const MODE='native-auto-review-v1';
const REVIEW_INSTRUCTION='Continue the same current owner request once using the normal native planner and the role limits below. '
  +'Tool use is optional: when no connected operation is needed, answer normally without calling an adapter. '
  +'The role is an upper effect limit, not consent, availability or a claim that the owner requested an operation. '
  +'Only configured builtin reads and exact current Ask-first approval preparation can be admitted. '
  +'No automatic write, notification, custom MCP execution, settings change or approval consumption is available. '
  +'Preserve all current owner resource, content, date, order and other restrictions. A write request alone never justifies a private prerequisite read. '
  +'Use actual tool results for facts and prerequisites; assistant claims are not receipts. '
  +'Only approval-required confirms a pending request; review-only-ready leaves an existing approval unused. '
  +'If a call is refused, explain its actual result and stop that action; do not claim completion or create a variant to evade the refusal. '
  +'A run with no tool calls is a legitimate normal result, not a failed recovery or tool success.';
const BLOCK=Object.freeze({block:true,blockReason:'This continuation is read/Ask-only. Only the two native integration adapters within their bounded review role may run.'});
const ADMISSION_BLOCK=Object.freeze({outcome:'block',reason:'ods-review-native-scope-unavailable',
  message:'Portal stopped this continuation because its restricted native tool scope could not be verified. Check actual results and approvals before continuing.'});
const identityString = value => typeof value === 'string' && value.length > 0 && value.length <= 2048
  && !/[\x00-\x20\x7f]/.test(value);
const portalSession = value => typeof value === 'string' && /^agent:pixel:openai-user:ods-[a-f0-9]{64}$/.test(value);
function identity(context) {
  return context?.agentId === 'pixel' && ['runId','sessionId','sessionKey'].every(key=>identityString(context[key]))
    ? {runId:context.runId,sessionId:context.sessionId,sessionKey:context.sessionKey} : undefined;
}
const same = (left,right) => left && right
  && ['runId','sessionId','sessionKey'].every(key=>left[key]===right[key]);
function plain(message) {
  if (typeof message?.content === 'string') return message.content;
  if (!Array.isArray(message?.content) || message.content.some(part=>!part || !['text','thinking'].includes(part.type))) return undefined;
  return message.content.filter(part=>part.type==='text').map(part=>typeof part.text==='string'?part.text:'').join('\n');
}
function zeroCurrentAttempts(messages,owner) {
  if (!Array.isArray(messages) || messages.length>2000) return false;
  const index=messages.findLastIndex(message=>message?.role==='user');
  if (index<0 || plain(messages[index])!==owner) return false;
  const rest=messages.slice(index+1);
  return rest.length>0 && rest.every(message=>message?.role==='assistant' && plain(message)!==undefined
    &&!(Array.isArray(message.tool_calls)&&message.tool_calls.length)&&!message.function_call);
}
function freezeTree(value) {
  if(value&&typeof value==='object'){for(const child of Object.values(value))freezeTree(child);Object.freeze(value);}
  return value;
}
function publicReferences(value) {
  const refs=snapshotIntegrationRequest(value);
  if(!refs||Object.keys(refs).sort().join(',')!=='lookups,truncated'||!Array.isArray(refs.lookups)
      ||typeof refs.truncated!=='boolean'||Buffer.byteLength(JSON.stringify(refs))>16384)throw Error('review-reference-bound');
  let count=0;
  for(const lookup of refs.lookups){
    if(!lookup||Object.keys(lookup).sort().join(',')!=='entries,query,source,truncated'
        ||!['obsidian','paperless','watch'].includes(lookup.source)||!Array.isArray(lookup.entries)
        ||!(lookup.query===null||typeof lookup.query==='string')||typeof lookup.truncated!=='boolean')throw Error('review-reference-shape');
    for(const entry of lookup.entries){
      if(!entry||typeof entry!=='object'||Array.isArray(entry))throw Error('review-reference-entry');
      const fields=Object.keys(entry).sort().join(',');
      if(lookup.source==='obsidian'){
        if(fields!=='path'||typeof entry.path!=='string'||!entry.path||Array.from(entry.path).length>512)throw Error('review-note-reference');
      }else{
        if(fields!==(lookup.source==='paperless'?'id,title':'id,title,url')||typeof entry.id!=='string'||!entry.id
            ||typeof entry.title!=='string'||Array.from(entry.title).length>200)throw Error('review-app-reference');
        if(lookup.source==='paperless'){
          if(!/^[1-9][0-9]{0,18}$/.test(entry.id)||BigInt(entry.id)>9223372036854775807n)throw Error('review-document-reference');
        }else if(Array.from(entry.id).length>128||typeof entry.url!=='string'||Array.from(entry.url).length>500)throw Error('review-watch-reference');
      }
      if(++count>64)throw Error('review-reference-count');
    }
  }
  return freezeTree(refs);
}
function publicProgress(value,state,context) {
  const progress=snapshotIntegrationRequest(value);
  if(!progress||Object.keys(progress).sort().join(',')!=='reads,truncated'||!Array.isArray(progress.reads)
      ||progress.reads.length>16||typeof progress.truncated!=='boolean'
      ||Buffer.byteLength(JSON.stringify(progress))>8192)throw Error('review-progress-bound');
  const sequences=new Set();
  for(const fact of progress.reads){
    if(!fact||Object.keys(fact).sort().join(',')!=='raw,sequence,status'||fact.status!=='read-complete'
        ||!Number.isInteger(fact.sequence)||fact.sequence<1||fact.sequence>256||sequences.has(fact.sequence)
        ||!fact.raw||Object.keys(fact.raw).sort().join(',')!=='name,params')throw Error('review-progress-shape');
    const {name,params}=fact.raw;
    const contentRead=name==='pixel_ods_notes'&&params?.action==='read'
      ||name==='pixel_ods_mcp'&&params?.action==='call'&&params.server==='app:documents'&&params.tool==='read';
    if(!contentRead||reviewIntegrationCandidate(name,params,context).maximumEffect!=='private-read'
        ||!operationAllowed(state,{toolName:name,params},context))throw Error('review-progress-read');
    sequences.add(fact.sequence);
  }
  return freezeTree(progress);
}
function operationAllowed(state,event,context,priorDecision) {
  if(state?.mode!==MODE)return false;
  let name=event?.toolName,params=priorDecision?.params??event?.params;
  if(name==='tool_call'){
    if(!canonicalIntegrations.has(params?.id))return false;
    name=params.id.slice('openclaw:pixel-ods:'.length);params=params.args;
  }
  if(!integrations.has(name))return false;
  try{
    const validated=reviewIntegrationCandidate(name,params,context);
    const role=nativeReviewOperation(validated.payload);
    return Boolean(role&&role.maximumEffect===validated.maximumEffect);
  }catch{return false;}
}

export function createIntegrationReviewRecovery({eligible=()=>false,ready=()=>false,nativeStartClear=()=>false,record=()=>{},maxRuns=128}={}) {
  const runs=new Map(),latest=new Map();let stopped=false;
  const stateFor=context=>runs.get(context?.runId);
  const owned=state=>!stopped&&state&&!state.ended&&!state.cancelled&&!state.conflict
    &&latest.get(state.sessionKey)===state.runId&&runs.get(state.runId)===state;
  const live=state=>{
    if(!owned(state))return false;
    try{if(eligible(state.runId)!==true||ready(state)!==true)return false;}catch{return false;}
    // Callbacks observe native state; they cannot revive a cancelled owner.
    return owned(state);
  };
  const clearToStart=state=>{try{return nativeStartClear(state)===true;}catch{return false;}};
  const toolCurrent=(state,context)=>Boolean(state?.mode===MODE&&live(state)&&!state.ioStopped&&same(state,identity(context))
    &&(context.modelProviderId===undefined||context.modelProviderId===state.modelProviderId)
    &&(context.modelId===undefined||context.modelId===state.modelId));
  const stopIO=(state,code)=>{if(state?.reviewOnly){state.ioStopped=true;state.ioStopCode=code;}};
  const knownLatch=(context,event)=>{
    const exact=stateFor(context);
    if(exact?.reviewOnly)return exact;
    const nativeEvent=runs.get(event?.runId);
    if(nativeEvent?.reviewOnly)return nativeEvent;
    // Missing/mismatched identity cannot turn an existing corrective pass into
    // normal execution. A new turn is normal only after trusted observation.
    if (context?.sessionKey) {
      const latestState=runs.get(latest.get(context.sessionKey));
      if(latestState?.reviewOnly)return latestState;
    }
    if (context?.sessionId) {
      for(const state of runs.values())if(state.reviewOnly && state.sessionId===context.sessionId
          && latest.get(state.sessionKey)===state.runId)return state;
    }
    return undefined;
  };
  function check(event,context,priorDecision){
    const id=identity(context),state=stateFor(context);
    if(knownLatch(context,event))return priorDecision?.action==='revise'?{action:'continue'}:priorDecision;
    if(!same(state,id)||!live(state)||state.attempted||state.checked||context?.trigger!=='user'
        ||event?.nativeRetryToolsAllowVersion!==1||event?.nativeRetryToolChoiceVersion!==1
        ||state.modelProviderId!=='ods-gateway'||state.modelId!=='ods/current'
        ||priorDecision&&priorDecision.action!=='continue'
        ||!same(id,{runId:event?.runId,sessionId:event?.sessionId,sessionKey:event?.sessionKey})
        ||context.modelProviderId!==state.modelProviderId||context.modelId!==state.modelId
        ||event.stopHookActive===true||typeof event.lastAssistantMessage!=='string'||!event.lastAssistantMessage.trim()
        ||event.lastAssistantMessage.length>12000||!zeroCurrentAttempts(event.messages,state.owner)||!clearToStart(state))return priorDecision;
    // The route-specific native capability is required, but REQUIRED choice is
    // deliberately not armed. There is no claim that absence of calls is intent.
    if(!live(state)||!clearToStart(state)||state.attempted||state.checked)return priorDecision;
    state.checked=true;state.reviewOnly=true;
    Object.defineProperty(state,'mode',{value:MODE,writable:false,configurable:false});
    try{record({phase:'native-auto-review',runId:state.runId,decision:'revise'});}catch{ /* Not authority. */ }
    if(!live(state))return {action:'continue'};
    return {action:'revise',reason:'One optional native continuation is available within the integration review role.',retry:{
      instruction:REVIEW_INSTRUCTION+'\nNative review role (maximum effects, data only):\n'+JSON.stringify(NATIVE_REVIEW_ROLE)
        +'\nCurrent owner request (data):\n'+JSON.stringify({ownerRequest:state.owner}),
      toolsAllow:['pixel_ods_notes','pixel_ods_mcp'],idempotencyKey:'ods-native-auto-review-v1',maxAttempts:1}};
  }
  function admitNativeCandidate(candidate,context){
    const state=stateFor(context),ref=candidate?.ref;
    const refused=code=>{stopIO(state,code);return Object.freeze({candidate:ref,admission:'refused',code});};
    if(!state?.reviewOnly||!toolCurrent(state,context))return refused('review-not-current');
    try{
      if(!candidate||!Object.isFrozen(candidate)||Object.keys(candidate).sort().join(',')!=='ref,view'
          ||!ref||typeof ref!=='object'||Array.isArray(ref)||!Object.isFrozen(ref)||!Object.isFrozen(candidate.view)
          ||Object.keys(candidate.view).sort().join(',')!=='maximumEffect,payload,progress,raw,references')throw Error('review-candidate');
      const raw=freezeTree(snapshotIntegrationRequest(candidate.view.raw));
      if(!raw||Object.keys(raw).sort().join(',')!=='name,params'||!integrations.has(raw.name)
          ||!operationAllowed(state,{toolName:raw.name,params:raw.params},context))throw Error('review-raw-role');
      const validated=reviewIntegrationCandidate(raw.name,raw.params,context);
      const payload=freezeTree(snapshotIntegrationRequest(candidate.view.payload));
      if(JSON.stringify(payload)!==JSON.stringify(validated.payload))throw Error('review-payload-drift');
      const role=nativeReviewOperation(payload);
      if(!role||role.maximumEffect!==validated.maximumEffect||candidate.view.maximumEffect!==role.maximumEffect)throw Error('review-effect-drift');
      publicReferences(candidate.view.references);publicProgress(candidate.view.progress,state,context);
    }catch{return refused('review-candidate-invalid');}
    if(!toolCurrent(state,context))return refused('review-not-current');
    // This private object means native-role admission, NEVER semantic entailment.
    // Outcomes must save this exact object/ref and independently reserve budgets
    // before a factory can take the one-use payload-bound token.
    return Object.freeze({candidate:ref,admission:'native-review-policy-v1'});
  }
  return {
    admitNativeCandidate,
    stopReview(context,code='review-stopped'){
      const state=stateFor(context);
      if(same(state,identity(context)))stopIO(state,typeof code==='string'&&/^[a-z][a-z0-9-]{0,63}$/.test(code)?code:'review-stopped');
    },
    admit(event,context){
      const states=[stateFor(context),runs.get(event?.runId)].filter(state=>state?.reviewOnly);
      if(!states.length)return;
      const state=states[0],deny=()=>{state.cancelled=true;return ADMISSION_BLOCK;};
      if(states.some(value=>value!==state)||!same(state,identity(context))||!live(state)
          ||context.modelProviderId!==state.modelProviderId||context.modelId!==state.modelId
          ||event?.runId!==undefined&&event.runId!==state.runId)return deny();
      let valid=false;
      try{
        const native=event?.nativeRetryToolsAllowState;
        valid=Object.isFrozen(native)&&native.version===1&&native.active===true&&native.ownerRunId===state.runId
          &&Array.isArray(native.toolsAllow)&&Object.isFrozen(native.toolsAllow)&&native.toolsAllow.length===integrations.size
          &&new Set(native.toolsAllow).size===integrations.size&&native.toolsAllow.every(name=>integrations.has(name))
          &&event.nativeRetryToolChoiceState===undefined;
      }catch{ /* Missing, required or malformed native proof never downgrades. */ }
      return valid?undefined:deny();
    },
    observe(event,context,trustedOwnerRunId){
      if(stopped)return;
      const id=identity(context),prior=stateFor(context);
      if(prior){
        if(!same(prior,id)||prior.modelProviderId!==context.modelProviderId||prior.modelId!==context.modelId)prior.conflict=true;
        return;
      }
      if(!id||context.trigger!=='user'||trustedOwnerRunId!==id.runId||!portalSession(id.sessionKey)
          ||!identityString(context.modelProviderId)||!identityString(context.modelId)
          ||typeof event?.prompt!=='string'||!event.prompt.trim()||event.prompt.length>16000)return;
      for(const [key,value]of runs){
        if(runs.size<maxRuns)break;
        if(!value.reviewOnly&&value.ended){runs.delete(key);if(latest.get(value.sessionKey)===key)latest.delete(value.sessionKey);}
      }
      if(runs.size>=maxRuns){if(runs.get(latest.get(id.sessionKey))?.reviewOnly)latest.set(id.sessionKey,id.runId);return;}
      const state={...id,attempted:false,checked:false,reviewOnly:false,ended:false,ioStopped:false};
      Object.defineProperties(state,{
        owner:{value:event.prompt,writable:false,configurable:false},
        modelProviderId:{value:context.modelProviderId,writable:false,configurable:false},
        modelId:{value:context.modelId,writable:false,configurable:false},
      });
      runs.set(id.runId,state);latest.set(id.sessionKey,id.runId);
    },
    beforeTool(event,context){const state=stateFor(context)??runs.get(event?.runId);if(state)state.attempted=true;},
    floor(event,context,priorDecision){
      if(priorDecision?.block)return priorDecision;
      const state=knownLatch(context,event);if(!state)return priorDecision;
      if(!same(state,identity(context))||!live(state)||state.ioStopped)return BLOCK;
      try{if(operationAllowed(state,event,context,priorDecision))return priorDecision;}catch{ /* closed */ }
      return BLOCK;
    },
    isReviewOnly(context,event){return Boolean(knownLatch(context,event));},
    async finalize(event,context,priorDecision){return check(event,context,priorDecision);},
    cancelSession(sessionKey){for(const state of runs.values())if(state.sessionKey===sessionKey)state.cancelled=true;},
    end(event,context){for(const state of new Set([stateFor(context),runs.get(event?.runId)]))if(state)state.ended=true;},
    async stop(){stopped=true;for(const state of runs.values())state.cancelled=true;},
  };
}
