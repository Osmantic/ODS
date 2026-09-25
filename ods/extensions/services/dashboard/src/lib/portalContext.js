/* eslint-disable no-control-regex -- Reject control bytes in untrusted public input. */
import {useCallback,useEffect,useRef,useState} from 'react'

export const CONTEXT_REQUEST_ID=/^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i
export const compactCommand=text=>typeof text==='string' && /^\/(?:compact|compactar)\s*$/i.test(text.trim())

export function historySnapshot(messages) {
  if(!Array.isArray(messages) || messages.length>2000)throw new Error('This conversation exceeds the 2,000-message history limit. Export it before starting a new chat.')
  let bytes=0
  const encoder=new TextEncoder()
  const history=messages.map(message=>{
    if(!message || !['user','assistant'].includes(message.role) || typeof message.content!=='string')throw new Error('Conversation history could not be prepared. No request was sent.')
    bytes+=encoder.encode(message.content).byteLength
    if(bytes>4*1024*1024)throw new Error('This conversation exceeds the 4 MB history limit. Export it before starting a new chat.')
    return {role:message.role,content:message.content}
  })
  return {schemaVersion:1,messages:history}
}

const token=(value,maximum=100_000_000)=>Number.isSafeInteger(value) && value>=0 && value<=maximum
const text=(value,max)=>typeof value==='string' && value.length>0 && value.length<=max && !/[\u0000-\u001f\u007f]/.test(value)
const routeFingerprint=value=>typeof value==='string' && value.length===64 && /^[a-f0-9]{64}$/.test(value)
export function parseConversationContext(value) {
  if(!value || value.schemaVersion!==1 || !['ready','missing','busy','unavailable'].includes(value.status))return null
  const {context,compaction,history,model}=value
  if(!compaction || !['idle','running','completed','skipped','failed','unknown'].includes(compaction.status)
    || compaction.requestId!==undefined && !/^[A-Za-z0-9_-]{1,128}$/.test(compaction.requestId)
    || compaction.count!==undefined && !token(compaction.count)
    || compaction.reason!==undefined && !text(compaction.reason,512)
    || ['tokensBefore','tokensAfter'].some(key=>compaction[key]!==undefined && !token(compaction[key])))return null
  if(!history || !Number.isSafeInteger(history.acknowledgedMessages) || history.acknowledgedMessages<0
    || history.status!==undefined && !['ready','pending','unknown'].includes(history.status)
    || history.revision!==null && !/^[a-f0-9]{64}$/i.test(history.revision || ''))return null
  if(context!==null && (!context || !token(context.used) || !token(context.window,10_000_000) || context.window<1
    || context.measuredAt!==undefined && (!text(context.measuredAt,40) || !Number.isFinite(Date.parse(context.measuredAt)))))return null
  if(model!==undefined && model!==null && (!text(model.id,512) || !text(model.provider,128) || !token(model.contextWindow,10_000_000) || model.contextWindow<1))return null
  if(model?.routeFingerprint!==undefined && !routeFingerprint(model.routeFingerprint))return null
  return value
}

function requestId() {
  if(globalThis.crypto?.randomUUID)return globalThis.crypto.randomUUID()
  if(!globalThis.crypto?.getRandomValues)throw new Error('This browser cannot safely identify a compaction request.')
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128
  const hex=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('')
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
}

/** Compaction belongs to the server session. The browser retains only its request
 * identity; neither the transcript nor an inferred model summary is replaced. */
export function usePortalContext({chatId,runtimeKey:legacyRuntimeKey,runtimeIdentity,capacity,initialRequestId,blocked,onPendingChange}) {
  const acceptedModel=useRef(null)
  const runtime=useRef({chatId,identity:{model:'',source:'',provider:'',routeFingerprint:''},epoch:0})
  const typed=runtimeIdentity!==undefined
  if(typed) {
    const previous=runtime.current
    const sameChat=previous.chatId===chatId
    const prior=sameChat?previous.identity:{model:'',source:'',provider:'',routeFingerprint:''}
    const supplied=Object.fromEntries(['model','source','provider'].map(key=>[key,text(runtimeIdentity?.[key],512)?runtimeIdentity[key]:'']))
    supplied.routeFingerprint=routeFingerprint(runtimeIdentity?.routeFingerprint)?runtimeIdentity.routeFingerprint:''
    const priorModel=prior.model || (sameChat?acceptedModel.current?.id:'') || ''
    const modelChanged=Boolean(priorModel && supplied.model && priorModel!==supplied.model)
    const sourceChanged=Boolean(prior.source && supplied.source && prior.source!==supplied.source)
    const priorRoute=prior.routeFingerprint || (sameChat?acceptedModel.current?.routeFingerprint:'') || ''
    // Source/capacity are populated by separate status requests. Missing fields
    // do not mean that the owner selected another runtime; retain the last
    // confirmed identity until an actual model/provider change is reported.
    const identity=modelChanged || sourceChanged?supplied:Object.fromEntries(Object.keys(prior).map(key=>[key,supplied[key] || prior[key]]))
    const changed=modelChanged || sourceChanged || Boolean(prior.provider && supplied.provider && prior.provider!==supplied.provider)
      || Boolean(supplied.routeFingerprint && (priorRoute ? priorRoute!==supplied.routeFingerprint : sameChat && acceptedModel.current))
    runtime.current={chatId,identity,epoch:previous.epoch+(changed?1:0)}
    if(!sameChat)acceptedModel.current=null
  }
  const runtimeKey=typed?`identity-${runtime.current.epoch}`:legacyRuntimeKey
  const [view,setView]=useState({chatId,runtimeKey,context:null,observedCapacity:null,phase:initialRequestId?'checking':'idle',notice:'',historyUnknown:false,resolving:false,recoveryNotice:''})
  const current=useRef({}),pending=useRef(initialRequestId || null),requests=useRef(new Set()),query=useRef(null),mutation=useRef(false)
  const refreshQueued=useRef(false)
  const generation=useRef(0),lastQuery=useRef(0),priorRuntime=useRef(runtimeKey)
  const priorChat=useRef(chatId),lastMeasurement=useRef(null),invalidatedMeasurement=useRef(null)
  const lastManual=useRef(initialRequestId || null)
  current.current={chatId,runtimeKey,runtimeIdentity:typed?runtime.current.identity:null,capacity,blocked,onPendingChange,historyUnknown:view.chatId===chatId && view.runtimeKey===runtimeKey && view.historyUnknown}
  const publish=useCallback(update=>setView(previous=>({...previous,...update,chatId:current.current.chatId,runtimeKey:current.current.runtimeKey})),[])
  const persist=useCallback(id=>{current.current.onPendingChange(id,current.current.chatId);pending.current=id},[])

  const accept=useCallback((snapshot,fromMutation=false)=>{
    const operation=snapshot.compaction,own=Boolean(pending.current) && operation.requestId===pending.current
    const terminal=['completed','skipped','failed'].includes(operation.status)
    const missing=fromMutation && snapshot.status==='missing' && operation.status==='idle'
    const unavailable=Boolean(pending.current) && snapshot.status==='unavailable' && operation.status==='idle'
    const elsewhere=fromMutation && snapshot.status==='busy' && !own
    const manual=own || Boolean(lastManual.current) && operation.requestId===lastManual.current
    const restarted=manual && operation.status==='unknown' && operation.reason==='runtime-restarted'
    const phase=restarted?'interrupted':missing?'skipped':unavailable?'unavailable':elsewhere?'busy':operation.status==='running'?'running':terminal && manual?operation.status
      :pending.current?'unknown':'idle'
    const expected=current.current.runtimeIdentity
    const modelMatches=!expected || (!expected.model || snapshot.model?.id===expected.model)
      && (!expected.provider || snapshot.model?.provider===expected.provider)
      && (!expected.routeFingerprint || snapshot.model?.routeFingerprint===expected.routeFingerprint)
      && (expected.source!=='local-switchboard' || !snapshot.model?.routeFingerprint)
    const measurement=snapshot.context?JSON.stringify([snapshot.model?.id,snapshot.model?.provider,snapshot.model?.routeFingerprint || null,snapshot.context.used,snapshot.context.window,snapshot.context.measuredAt || null]):null
    const context=['ready','busy'].includes(snapshot.status) && snapshot.context
      && (!snapshot.model || snapshot.model.contextWindow===snapshot.context.window)
      && modelMatches && measurement!==invalidatedMeasurement.current?snapshot.context:null
    if(context){lastMeasurement.current=measurement;acceptedModel.current=snapshot.model || null}
    let notice=missing?'No runtime context exists yet. Send a message before compacting.'
      :phase==='interrupted'?'Runtime restarted before compaction could be confirmed. Your conversation is preserved; you can continue.'
      :phase==='running'?'Compacting conversation context…'
      :phase==='completed'?'Context compacted. Your full conversation is preserved.'
      :phase==='skipped'?(operation.reason==='missing-transcript'?'Context was not compacted. Your conversation is unchanged.':'No compaction was needed. Your conversation is unchanged.')
      :phase==='failed'?'Context compaction failed. Your conversation is preserved.'
      :phase==='busy'?'Compaction can start after the current task finishes.'
      :phase==='unavailable'?'Context compaction is unavailable. Your conversation is preserved.'
      :phase==='unknown'?'Waiting for compaction confirmation…':''
    const reasons={'missing-transcript':'The runtime transcript is unavailable.','unsupported-model':'The current runtime cannot compact this model.','runtime-failed':'The runtime could not complete the operation.','coordination-failed':'The runtime could not safely start the operation.'}
    if(reasons[operation.reason] && ['failed','skipped'].includes(phase))notice+=` ${reasons[operation.reason]}`
    if(pending.current && (own && (terminal || restarted) || missing || elsewhere || fromMutation && snapshot.status==='busy' && operation.status!=='running')) {
      try {persist(null)}catch {notice+=' The result could not be saved in this browser; reload to check it again.'}
    }
    const historyUnknown=snapshot.history.status==='unknown'
    publish({context,phase,notice,historyUnknown,...(snapshot.model?{observedCapacity:modelMatches?snapshot.model.contextWindow:null}:{}),...(!historyUnknown?{recoveryNotice:''}:{})})
  },[persist,publish])

  const refresh=useCallback(async(force=false)=>{
    if(mutation.current)return
    if(query.current){if(force)refreshQueued.current=true;return query.current}
    if(!force && Date.now()-lastQuery.current<1500)return
    const at=generation.current,identity=current.current.chatId
    const run=(async()=>{
      try {
        do {
          refreshQueued.current=false
          lastQuery.current=Date.now()
          const controller=new AbortController()
          requests.current.add(controller)
          const timer=setTimeout(()=>controller.abort(),15000)
          try {
            const response=await fetch('/api/pixel/chat/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:identity}),signal:controller.signal})
            const snapshot=parseConversationContext(await response.json())
            if(at!==generation.current)return
            if(!snapshot || !response.ok && response.status!==423)throw new Error('context unavailable')
            // A turn may finish while an earlier read is in flight. Coalesce
            // forced refreshes into a later read and do not publish that old
            // snapshot as the completed turn's current measurement.
            if(!refreshQueued.current)accept(snapshot)
          }catch {
            if(at===generation.current && !refreshQueued.current)publish({...(pending.current?{phase:'unknown',notice:'Waiting for compaction confirmation…'}:{})})
          }finally {clearTimeout(timer);requests.current.delete(controller)}
        }while(at===generation.current && refreshQueued.current && !mutation.current)
      }finally {if(at===generation.current)query.current=null}
    })()
    query.current=run
    return run
  },[accept,publish])

  useEffect(()=>{
    generation.current+=1;requests.current.forEach(controller=>controller.abort());requests.current.clear();query.current=null;refreshQueued.current=false;mutation.current=false;lastQuery.current=0
    pending.current=initialRequestId || null
    if(priorChat.current!==chatId){lastMeasurement.current=null;invalidatedMeasurement.current=null;lastManual.current=pending.current}
    else if(priorRuntime.current && priorRuntime.current!==runtimeKey)invalidatedMeasurement.current=lastMeasurement.current
    priorChat.current=chatId
    publish({context:null,observedCapacity:null,phase:pending.current?'checking':'idle',notice:pending.current?'Checking compaction status…':'',historyUnknown:false,resolving:false,recoveryNotice:''})
    priorRuntime.current=runtimeKey
    void refresh(true)
    return ()=>{generation.current+=1;requests.current.forEach(controller=>controller.abort());requests.current.clear();query.current=null;refreshQueued.current=false}
    // A pending identity is restored only when changing sessions, not on autosave.
  },[chatId,runtimeKey,publish,refresh])

  useEffect(()=>{
    if(view.chatId!==chatId || view.runtimeKey!==runtimeKey || !(pending.current || ['running','busy'].includes(view.phase) || view.historyUnknown))return
    const timer=setTimeout(()=>void refresh(true),['unknown','unavailable'].includes(view.phase)?5000:2000)
    return ()=>clearTimeout(timer)
  },[view,chatId,runtimeKey,refresh])

  const compact=useCallback(async()=>{
    if(current.current.blocked || current.current.historyUnknown || mutation.current)return false
    generation.current+=1;requests.current.forEach(controller=>controller.abort());requests.current.clear();query.current=null;refreshQueued.current=false
    const at=generation.current,identity=current.current.chatId
    let id=pending.current
    try {if(!id){id=requestId();persist(id)}}catch(error){publish({phase:'failed',notice:error?.message || 'The request could not be saved. No compaction was started.'});return false}
    lastManual.current=id
    mutation.current=true;publish({phase:'requesting',notice:'Compacting conversation context…'})
    const controller=new AbortController();requests.current.add(controller)
    const timer=setTimeout(()=>controller.abort(),30000)
    let requery=false
    try {
      const response=await fetch('/api/pixel/chat/compact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:identity,request_id:id}),signal:controller.signal})
      const snapshot=parseConversationContext(await response.json())
      if(at!==generation.current)return false
      if(!snapshot && [400,401,403,404,409,412,413,423,429].includes(response.status)) {
        persist(null)
        publish({phase:response.status===423?'busy':'failed',notice:response.status===423
          ?'Compaction can start after the current task finishes.'
          :'The runtime did not accept compaction. Your conversation is unchanged; check availability and try again.'})
        return false
      }
      if(!snapshot || !response.ok && response.status!==423)throw new Error('unconfirmed')
      accept(snapshot,true)
      requery=true
      return true
    }catch {
      requery=true
      if(at===generation.current)publish({phase:'unknown',notice:'Waiting for compaction confirmation…'})
      return false
    }finally {clearTimeout(timer);requests.current.delete(controller);if(at===generation.current){mutation.current=false;if(requery)void refresh(true)}}
  },[accept,persist,publish,refresh])

  const resolveInterrupted=useCallback(async()=>{
    if(current.current.blocked || !current.current.historyUnknown || mutation.current || pending.current)return false
    generation.current+=1;requests.current.forEach(controller=>controller.abort());requests.current.clear();query.current=null;refreshQueued.current=false
    const at=generation.current,identity=current.current.chatId,controller=new AbortController()
    mutation.current=true;requests.current.add(controller)
    publish({resolving:true,recoveryNotice:'Confirming the previous turn has stopped…'})
    const timer=setTimeout(()=>controller.abort(),15000)
    try {
      // This explicit owner action resolves uncertain history. Merely loading
      // a conversation or reading its context must never cancel work.
      const response=await fetch('/api/pixel/chat/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:identity}),signal:controller.signal})
      const result=await response.json()
      if(at!==generation.current)return false
      if(!response.ok || result?.aborted!==true)throw new Error('stop unconfirmed')
      publish({recoveryNotice:'Stop confirmed. Checking conversation context…'})
      return true
    }catch {
      if(at===generation.current)publish({recoveryNotice:'The stop could not be confirmed. Your conversation is preserved; check status or try again.'})
      return false
    }finally {
      clearTimeout(timer);requests.current.delete(controller)
      if(at===generation.current){mutation.current=false;publish({resolving:false});void refresh(true)}
    }
  },[publish,refresh])

  const displayed=view.chatId===chatId && view.runtimeKey===runtimeKey?view:{context:null,observedCapacity:null,phase:initialRequestId?'checking':'idle',notice:'',historyUnknown:false,resolving:false,recoveryNotice:''}
  return {...displayed,busy:displayed.resolving || ['requesting','checking','running','unknown'].includes(displayed.phase),canResolve:displayed.historyUnknown && !blocked && !pending.current && !displayed.resolving,compact,refresh,resolveInterrupted}
}
