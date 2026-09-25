import {useEffect,useRef,useState} from 'react'

const phases=new Set(['idle','completed','prepared','held','applying','applied','committing','rolling-back','unavailable'])
const modelName=/^[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}$/
function restoreOffer(value) {
  if(!value || typeof value!=='object' || typeof value.model!=='string' || !modelName.test(value.model)
      || !Number.isInteger(value.contextLength) || value.contextLength<4096 || value.contextLength>10000000)return null
  return {model:value.model,contextLength:value.contextLength}
}
function recovery(value) {
  if (!value || typeof value.pending!=='boolean' || !phases.has(value.phase)
      || value.pending !== !['idle','completed'].includes(value.phase)) return null
  if(value.transactionId!=null && (typeof value.transactionId!=='string'
      || value.transactionId.length!==64 || !/^[a-f0-9]+$/.test(value.transactionId)))return null
  if(!['idle','unavailable'].includes(value.phase) && value.transactionId==null)return null
  const restore=value.pending && value.transactionId ? restoreOffer(value.restore) : null
  const result={...value}
  delete result.restore
  return restore ? {...result,restore} : result
}
const unconfirmed='Recovery could not be confirmed. Reopen the model menu to read the current state.'
const context=tokens=>tokens%1024===0 ? `${tokens/1024}K` : tokens.toLocaleString()

/**
 * Recovery verifies the existing transaction; it never starts a new model load.
 * When it cannot prove either outcome, the owner may explicitly restore the
 * switch's own previous model (named by the host), which reloads that model
 * and releases the same transaction as a rollback.
 */
export default function PortalModelRecovery({onPendingChange,onBusyChange,onRecovered,refreshKey=0,active=true}) {
  const [state,setState]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState('')
  const [offerRestore,setOfferRestore]=useState(false)
  const mounted=useRef(false),request=useRef(null),locked=useRef(false)
  const callbacks=useRef({onPendingChange,onBusyChange,onRecovered})
  callbacks.current={onPendingChange,onBusyChange,onRecovered}
  useEffect(()=>{
    mounted.current=true
    return ()=>{mounted.current=false;request.current?.abort();callbacks.current.onBusyChange?.(false)}
  },[])
  useEffect(()=>{
    if(!active || locked.current)return
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),6000)
    void fetch('/api/models/recovery',{signal:controller.signal}).then(async response=>{
      // The API also projects validated pending receipts on 409/503. Other
      // failures are not recovery state and must never invent an outcome.
      if(!response.ok && ![409,503].includes(response.status))return
      const value=recovery(await response.json())
      if(!controller.signal.aborted && value){setState(value);callbacks.current.onPendingChange?.(value.pending)}
    }).catch(()=>{}).finally(()=>clearTimeout(timer))
    return ()=>{controller.abort();clearTimeout(timer)}
  },[refreshKey,active])
  async function submit(kind,url,body,deadline,explain) {
    if(locked.current || !state?.pending)return
    locked.current=true;setBusy(kind);setError('');callbacks.current.onBusyChange?.(true)
    const controller=new AbortController();request.current=controller
    const timer=setTimeout(()=>controller.abort(),deadline)
    try {
      const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body,signal:controller.signal})
      const value=recovery(await response.json())
      if(!mounted.current || controller.signal.aborted)return
      if(value){setState(value);callbacks.current.onPendingChange?.(value.pending)}
      if(response.ok && value && !value.pending){setOfferRestore(false);callbacks.current.onRecovered?.();return}
      setError(explain(value))
    } catch {
      if(mounted.current)setError(unconfirmed)
    } finally {
      clearTimeout(timer);request.current=null;locked.current=false
      if(mounted.current){setBusy('');callbacks.current.onBusyChange?.(false)}
    }
  }
  function recover() {
    return submit('recover','/api/models/recovery','{}',405000,value=>{
      if(value?.reason!=='model-recovery-proof-required')return unconfirmed
      setOfferRestore(true)
      return 'The interrupted switch still needs repair. The saved state has been preserved.'
    })
  }
  function restore() {
    const offer=state?.restore
    if(!offer)return
    return submit('restore','/api/models/recovery/restore',JSON.stringify({transactionId:state.transactionId}),2705000,value=>{
      if(value?.reason==='model-restore-failed')
        return `Restoring ${offer.model} did not finish${value.detail?`: ${value.detail}`:'.'} The switch is still pending; you can try again.`
      if(value?.reason==='model-restore-unavailable')
        return 'The previous model can no longer be restored automatically. The switch is still pending.'
      if(value?.reason==='model-recovery-unavailable' && value.restore)
        return 'The model controller did not answer, so nothing was changed. Try the restore again shortly.'
      return unconfirmed
    })
  }
  if(!state?.pending)return null
  const offer=state.restore
  return <div className="portal-model-notice">
    <p>A previous model switch was interrupted. Verify it before continuing.</p>
    <button type="button" disabled={Boolean(busy)} onClick={recover}>{busy==='recover'?'Recovering…':'Recover model switch'}</button>
    {error && <p role="alert">{error}</p>}
    {offerRestore && (offer
      ? <>
          <p>Repair could not prove that the switch finished or was undone. Restoring reloads {offer.model} at {context(offer.contextLength)} context, the model that was active before the switch, and then releases it.</p>
          <button type="button" disabled={Boolean(busy)} onClick={restore}>{busy==='restore'?'Restoring…':`Restore ${offer.model}`}</button>
        </>
      : <p>The previous model cannot be restored automatically on this installation. Keep this switch pending and check the host agent log.</p>)}
  </div>
}
