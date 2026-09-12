import {useEffect,useRef,useState} from 'react'

export default function TalkReplyCopy({text}) {
  const [state,setState]=useState('idle')
  const version=useRef(0)
  const pending=useRef(false)
  useEffect(()=>()=>{version.current++},[])
  async function copy(){
    if(pending.current)return
    pending.current=true
    const current=++version.current
    setState('copying')
    let timer
    try {
      await Promise.race([navigator.clipboard.writeText(text),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Clipboard deadline')),10000)})])
      if(current===version.current)setState('copied')
    } catch {if(current===version.current)setState('failed')}
    finally {clearTimeout(timer);pending.current=false}
  }
  return <div className="mt-2 text-xs">
    <button type="button" onClick={copy} disabled={state==='copying'} className="rounded border border-zinc-300 px-2 py-1">{state==='copying'?'Copying reply?':'Copy reply'}</button>
    {state==='copied'&&<p role="status">Reply copied.</p>}
    {state==='failed'&&<><p role="alert">Clipboard unavailable or unconfirmed. Select the Markdown below to copy manually.</p><textarea aria-label="Reply Markdown for manual copying" className="mt-2 h-28 w-full bg-white p-2 font-mono" readOnly value={text} onFocus={e=>e.target.select()}/></>}
  </div>
}
