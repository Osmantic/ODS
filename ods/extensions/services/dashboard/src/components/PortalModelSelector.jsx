import {useEffect,useId,useRef,useState} from 'react'
import {Check,ChevronDown,Loader2,SlidersHorizontal} from 'lucide-react'
import {Link} from 'react-router-dom'
import {useModels} from '../hooks/useModels'
import PortalModelRecovery from './PortalModelRecovery'
import './portal-model-selector.css'

export function modelDisplayName(model,compact=false) {
  let value=String(typeof model==='string'?model:model?.name || model?.id || '').trim()
  if(compact && /^extra\./i.test(value)) {
    const family=value.match(/\b([a-z]+[\d.]*-\d+(?:\.\d+)?[bm])\b/i)
    if(family)value=family[1]
  }
  value=value.replace(/^extra\.(?:hf[-.])?/i,'').replace(/\.gguf$/i,'').replace(/[-_.][a-f\d]{8,64}$/i,'')
    .replace(/\s*·\s*(?:I?Q\d[\w.]*|[BF]F?16|AWQ|GPTQ).*$/i,'')
    .replace(/(?:^|[-\s])(?:I?Q\d(?:_[A-Z\d]+)*)(?=$|[-\s])/gi,' ')
    .replace(/\bGGUF\b/gi,'').replace(/[-_]+/g,' ').replace(/([a-z])(\d)/gi,'$1 $2').replace(/\s+/g,' ').trim()
  if(compact)value=value.match(/^.*?\b\d+(?:\.\d+)?[BM]\b/i)?.[0] || value
  return value || 'Choose model'
}

function details(model) {
  const context=Number(model.contextLength)
  return [model.quantization,Number.isFinite(context) && context>0?`${Number((context/1024).toFixed(1))}K context`:null].filter(Boolean).join(' · ')
}

function verifiedNativeProfile(model) {
  const support=model.activationSupport
  return support?.available===true && support.source==='measured-native' && support.mode==='native-profile'
    && Number.isInteger(support.contextLength) && support.contextLength===model.contextLength
}

/** A closed, unused selector must not start model-catalog requests or polling. */
export default function PortalModelSelector(props) {
  const [started,setStarted]=useState(false)
  if(started)return <LoadedModelSelector {...props}/>
  return <div className="portal-model-selector"><button type="button" className="portal-model-trigger"
    aria-label={`Choose model: ${modelDisplayName(props.activeModel,true)}`} aria-haspopup="dialog" aria-expanded="false"
    title={modelDisplayName(props.activeModel)} onClick={()=>setStarted(true)}>
    <span>{modelDisplayName(props.activeModel,true)}</span><ChevronDown size={12} aria-hidden="true"/>
  </button></div>
}

/** Once opened, retain the hook even while closed so an accepted swap stays observed. */
function LoadedModelSelector({activeModel='',runtimeSource,busy=false,onSwitchingChange,onSettled}) {
  const catalog=useModels()
  const {currentModel,activationReadyModel,loading,error,canActivateModels,activationModeError,activationLoading,modelLifecycle,actionLoadingModels=[],loadModel,refresh,clearMutationError}=catalog
  const models=Array.isArray(catalog.models)?catalog.models:[]
  const installed=models.filter(model=>model && typeof model.id==='string' && ['loaded','downloaded'].includes(model.status))
  const [open,setOpen]=useState(true),[confirmId,setConfirmId]=useState(null),[pending,setPending]=useState(false),[localError,setLocalError]=useState('')
  const [recoveryPending,setRecoveryPending]=useState(false),[recoveryBusy,setRecoveryBusy]=useState(false)
  const root=useRef(null),trigger=useRef(null),list=useRef(null),mounted=useRef(true),submitLock=useRef(false)
  const id=useId(),remote=runtimeSource==='remote-provider',local=runtimeSource==='local-switchboard'
  const switching=pending || recoveryBusy || Boolean(activationLoading) || Boolean(modelLifecycle?.active && modelLifecycle.operation==='model_activation')
  const current=local?models.find(model=>model.id===currentModel):null
  const selectedId=local && activationReadyModel===currentModel?currentModel:null
  const activeName=current || activeModel
  const confirmation=installed.find(model=>model.id===confirmId)
  const switchingCallback=useRef(onSwitchingChange)
  switchingCallback.current=onSwitchingChange
  useEffect(()=>{onSwitchingChange?.(switching)},[switching,onSwitchingChange])
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;switchingCallback.current?.(false)}},[])
  function close(focus=false) {setOpen(false);setConfirmId(null);if(focus)trigger.current?.focus()}
  useEffect(()=>{
    if(!open)return
    const outside=event=>{if(!root.current?.contains(event.target))close()}
    const escape=event=>{if(event.key==='Escape'){event.preventDefault();close(true)}}
    window.addEventListener('pointerdown',outside);window.addEventListener('keydown',escape)
    return ()=>{window.removeEventListener('pointerdown',outside);window.removeEventListener('keydown',escape)}
  },[open])
  useEffect(()=>{
    if(!open)return
    if(confirmId)root.current?.querySelector('.portal-model-confirmation button')?.focus()
    else list.current?.querySelector('[aria-checked=true],button:not(:disabled)')?.focus()
  },[open,confirmId,loading])
  function unavailable(model) {
    if(switching || modelLifecycle?.active || actionLoadingModels.length)return 'A model operation is in progress.'
    if(recoveryPending)return 'Recover the interrupted model switch before loading another model.'
    if(remote)return 'This conversation uses a remote provider. Choose its model in provider settings.'
    if(!local)return 'The conversation’s model source is not confirmed. Review Models before switching.'
    if(busy)return 'Wait for the active task to finish before switching models.'
    if(!canActivateModels)return activationModeError || 'Model switching is unavailable for this runtime.'
    if(model.fitsVram!==true && !model.recommended && !verifiedNativeProfile(model))return 'Review this model’s memory requirements in Models before loading it.'
    return ''
  }
  async function activate() {
    if(!confirmation || unavailable(confirmation) || submitLock.current)return
    submitLock.current=true;setPending(true);setLocalError('');setConfirmId(null);onSwitchingChange?.(true)
    try {
      const contextLength=Number(confirmation.contextLength)
      if(!Number.isSafeInteger(contextLength) || contextLength<4096 || contextLength>10_000_000)
        throw new Error('The model context is unavailable. Refresh the model list before switching.')
      // Activate exactly the context shown in the confirmation, not a catalog default.
      await loadModel(confirmation.id,{contextLength})
    }
    catch (failure) {if(mounted.current)setLocalError(failure?.message || 'The model could not be activated.')}
    finally {
      submitLock.current=false
      if(mounted.current){setPending(false);onSettled?.()}
    }
  }
  const reason=switching?'':confirmation?unavailable(confirmation):remote?'This conversation uses a remote provider.':!local?'The conversation’s model source is not confirmed.':busy?'The current task is still running.':!canActivateModels && !loading?activationModeError:''
  const showActiveFallback=activeModel && !current
  return <div ref={root} className="portal-model-selector">
    <button ref={trigger} type="button" className="portal-model-trigger" aria-label={`Choose model: ${modelDisplayName(activeName,true)}`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open?id:undefined} title={modelDisplayName(activeName)} onClick={()=>{if(open)close();else {setOpen(true);void refresh()}}}>
      {switching && <Loader2 size={12} className="portal-model-loading" aria-hidden="true"/>}<span>{modelDisplayName(activeName,true)}</span><ChevronDown size={12} aria-hidden="true"/>
    </button>
    <section id={id} role="dialog" aria-label="Choose model" className="portal-model-menu" hidden={!open} style={!open?{display:'none'}:undefined}>
      <header><strong>{confirmation?'Switch model':'Model'}</strong>{switching && <span role="status">Switching…</span>}</header>
      {confirmation?<div className="portal-model-confirmation">
        <p>Switch to <strong>{modelDisplayName(confirmation)}</strong>?</p><small>This changes the active model across ODS.</small>
        {details(confirmation) && <small>{details(confirmation)}</small>}
        {reason && <p role="status">{reason}</p>}
        <div><button type="button" onClick={()=>setConfirmId(null)}>Cancel</button><button type="button" className="portal-model-confirm" disabled={Boolean(reason)} onClick={activate}>Switch model</button></div>
      </div>:<>
        <div ref={list} role="menu" aria-label="Installed models" className="portal-model-options" onKeyDown={event=>{
          if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return
          event.preventDefault()
          const options=[...event.currentTarget.querySelectorAll('button:not(:disabled)')],index=options.indexOf(document.activeElement)
          const next=event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length
          options[next]?.focus()
        }}>
          {showActiveFallback && <div role="menuitemradio" aria-checked="true" className="portal-model-option"><span><strong>{modelDisplayName(activeModel)}</strong><small>{remote?'Remote provider':'Active model'}</small></span><Check size={15} aria-hidden="true"/></div>}
          {installed.map(model=>{const selected=model.id===selectedId,disabled=unavailable(model);return <button key={model.id} role="menuitemradio" aria-checked={selected} type="button" className="portal-model-option" disabled={!selected && Boolean(disabled)} title={disabled || modelDisplayName(model)} onClick={()=>{if(selected)close(true);else if(!disabled)setConfirmId(model.id)}}><span><strong>{modelDisplayName(model)}</strong><small>{details(model)}</small></span>{model.id===activationLoading?<Loader2 size={15} className="portal-model-loading" aria-hidden="true"/>:selected?<Check size={15} aria-hidden="true"/>:null}</button>})}
        </div>
        {loading && <p role="status" className="portal-model-notice">Loading models…</p>}
        {!loading && !installed.length && !showActiveFallback && <p className="portal-model-notice">No installed models found.</p>}
        {reason && <p className="portal-model-notice" role="status">{reason}</p>}
        {(localError || error) && <p role="alert" className="portal-model-notice">{localError || error} <button type="button" onClick={()=>void refresh()}>Refresh</button></p>}
        <Link className="portal-model-manage" to={remote?'/pixel/settings?section=connections':'/models'}><SlidersHorizontal size={14} aria-hidden="true"/>{remote?'Provider settings':'Manage models'}</Link>
      </>}
      <PortalModelRecovery active={open} refreshKey={`${pending}:${Boolean(activationLoading)}`} onPendingChange={setRecoveryPending} onBusyChange={setRecoveryBusy} onRecovered={()=>{setLocalError('');clearMutationError();void refresh();onSettled?.()}}/>
    </section>
  </div>
}
