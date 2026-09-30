import {useEffect,useRef,useState} from 'react'

// No transcript persistence, automatic input or model access. This panel is
// application chrome; never mount it inside the untrusted Preview iframe.
export function terminalText(value) {
  return value.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'').replace(/\x1b\][\s\S]*?(?:\x07|\x1b\\)/g,'').replace(/[\x00-\x08\x0b-\x1f\x7f]/g,'')
}

export default function PortalApprovalTerminal({job,plan}) {
  const [opened,setOpened]=useState(false),[output,setOutput]=useState(''),[error,setError]=useState('')
  const [state,setState]=useState('idle'),[busy,setBusy]=useState(false)
  const session=useRef(null),sequence=useRef(0),cursor=useRef(0),input=useRef(null),alive=useRef(true)
  async function request(body) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000)
    try {
      const response=await fetch('/api/pixel/approval-terminal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal})
      const value=await response.json()
      if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'Approval terminal unavailable.')
      return value
    }finally{clearTimeout(timer)}
  }
  useEffect(()=>{
    alive.current=true
    return()=>{alive.current=false;if(session.current)void request({action:'cancel',session:session.current}).catch(()=>{})}
  },[])
  useEffect(()=>{
    if(!opened || !session.current || state!=='running')return
    let stopped=false,timer
    const poll=async()=>{
      try {
        const value=await request({action:'poll',session:session.current,cursor:cursor.current})
        if(stopped)return
        if(typeof value.output!=='string' || typeof value.state!=='string' || !Number.isInteger(value.nextCursor) || value.nextCursor<cursor.current)throw new Error('Terminal response was not confirmed.')
        setOutput(previous=>previous+value.output);cursor.current=value.nextCursor
        if(value.state!=='running' && value.exitCode!==null && value.cleanupConfirmed && value.moreOutput===false){setState(value.state);return}
        timer=setTimeout(poll,300)
      }catch(e){if(!stopped){setError(e.message);setState('unconfirmed')}}
    }
    void poll()
    return()=>{stopped=true;clearTimeout(timer)}
  },[opened,state])
  async function open() {
    setBusy(true);setError('')
    try {
      const result=await request({action:'start',job,plan})
      if(typeof result.session!=='string' || !/^[a-f0-9]{64}$/.test(result.session))throw new Error('Terminal creation was not confirmed.')
      session.current=result.session;sequence.current=0;cursor.current=0
      if(!alive.current){await request({action:'cancel',session:result.session});return}
      setOutput('');setState('running');setOpened(true)
    }catch(e){if(alive.current)setError(e.message)}finally{if(alive.current)setBusy(false)}
  }
  async function submit(event) {
    event.preventDefault()
    if(busy || state!=='running' || !input.current?.value)return
    const line=input.current.value;input.current.value='';setBusy(true)
    try {
      const result=await request({action:'input',session:session.current,sequence:sequence.current,line})
      if(result.accepted!==true || result.nextSequence!==sequence.current+1)throw new Error('Input acknowledgement unavailable. Do not resend the password; cancel and start again.')
      sequence.current=result.nextSequence
    }catch(e){setError(e.message);setState('unconfirmed')}finally{setBusy(false)}
  }
  async function cancel() {
    setBusy(true)
    try {
      const result=await request({action:'cancel',session:session.current})
      if(result.stopped!==true || result.cleanupConfirmed!==true)throw new Error('Stop or credential cleanup was not confirmed. Use your local terminal to check the job.')
      setState('cancelled');session.current=null
    }catch(e){setError(e.message)}finally{setBusy(false)}
  }
  return <div className="mt-3 rounded-xl border border-theme-border bg-theme-bg-primary p-3">
    {!opened?<button type="button" disabled={busy} onClick={open} className="rounded-lg bg-theme-accent px-3 py-2 text-sm">{busy?'Opening…':'Open approval terminal'}</button>:<section aria-label="Protected approval terminal">
      <h4 className="text-sm font-medium">Owner approval terminal</h4>
      <p className="mt-1 text-xs text-theme-text-muted">Read the complete protected plan below. Enter your host sudo password only at its prompt, then type the exact one-time confirmation. Nothing is sent to the model.</p>
      <pre role="log" aria-label="Private terminal output" className="my-3 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black p-3 font-mono text-xs text-emerald-200">{terminalText(output)}</pre>
      {state==='running' && <form onSubmit={submit} className="flex gap-2">
        <input ref={input} type="password" autoComplete="off" maxLength={1024} aria-label="Private terminal input" placeholder="Password or exact confirmation" className="min-w-0 flex-1 rounded-lg border border-theme-border bg-theme-bg-secondary p-2"/>
        <button disabled={busy} type="submit">Send input</button>
      </form>}
      <p role="status" className="mt-2 text-xs text-theme-text-muted">{state==='running'?'Waiting for owner input':`Terminal ${state}. Approval and operation results are reported separately by the broker.`}</p>
      {session.current && <div className="mt-2"><p className="text-xs text-theme-text-muted">Stopping this terminal does not cancel an already approved operation.</p><button type="button" disabled={busy} onClick={cancel} className="mt-2 text-xs">Stop terminal</button></div>}
      {!session.current && state==='cancelled' && <button type="button" disabled={busy} onClick={open} className="mt-2 text-sm">Open new approval terminal</button>}
    </section>}
    {error && <p role="alert" className="mt-2 text-xs text-red-400">{error}</p>}
  </div>
}
