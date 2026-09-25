import {useEffect,useRef,useState} from 'react'

const phases=new Set(['idle','completed','prepared','held','applying','applied','committing','rolling-back','unavailable'])
function recovery(value) {
  if (!value || typeof value.pending!=='boolean' || !phases.has(value.phase)
      || value.pending !== !['idle','completed'].includes(value.phase)) return null
  if(value.transactionId!=null && (typeof value.transactionId!=='string'
      || value.transactionId.length!==64 || !/^[a-f0-9]+$/.test(value.transactionId)))return null
  if(!['idle','unavailable'].includes(value.phase) && value.transactionId==null)return null
  return value
}

/** Recovery verifies the existing transaction; it never starts a new model load. */
export default function PortalModelRecovery({onPendingChange,onBusyChange,onRecovered,refreshKey=0,active=true}) {
  const [state,setState]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
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
  async function recover() {
    if(locked.current || !state?.pending)return
    locked.current=true;setBusy(true);setError('');callbacks.current.onBusyChange?.(true)
    const controller=new AbortController();request.current=controller
    const timer=setTimeout(()=>controller.abort(),405000)
    try {
      const response=await fetch('/api/models/recovery',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:controller.signal})
      const value=recovery(await response.json())
      if(!mounted.current || controller.signal.aborted)return
      if(value){setState(value);callbacks.current.onPendingChange?.(value.pending)}
      if(response.ok && value && !value.pending){callbacks.current.onRecovered?.();return}
      setError(value?.reason==='model-recovery-proof-required'
        ? 'The interrupted switch still needs repair. The saved state has been preserved.'
        : 'Recovery could not be confirmed. Reopen the model menu to read the current state.')
    } catch {
      if(mounted.current)setError('Recovery could not be confirmed. Reopen the model menu to read the current state.')
    } finally {
      clearTimeout(timer);request.current=null;locked.current=false
      if(mounted.current){setBusy(false);callbacks.current.onBusyChange?.(false)}
    }
  }
  if(!state?.pending)return null
  return <div className="portal-model-notice">
    <p>A previous model switch was interrupted. Verify it before continuing.</p>
    <button type="button" disabled={busy} onClick={recover}>{busy?'Recovering…':'Recover model switch'}</button>
    {error && <p role="alert">{error}</p>}
  </div>
}
