import {useEffect, useRef, useState} from 'react'
import {parseConversationImport} from '../lib/pixelConversationImport'

export default function PixelConversationImport({disabled, onImport}) {
  const field=useRef(null), reader=useRef(null), dialog=useRef(null), trigger=useRef(null)
  const [pending,setPending]=useState(null)
  const [error,setError]=useState('')
  const [reading,setReading]=useState(false)
  useEffect(()=>()=>{if(reader.current){reader.current.onload=null;reader.current.onerror=null;reader.current.abort()}},[])
  function close(){if(reader.current){reader.current.onload=null;reader.current.onerror=null;reader.current.abort();reader.current=null}setReading(false);dialog.current?.close();setPending(null);setError('');trigger.current?.focus()}
  function choose(event){
    const file=event.target.files?.[0];event.target.value=''
    if(!file)return
    setPending(null);setError('');dialog.current.showModal()
    if(!file.size || file.size>32*1024*1024){setError('Choose a nonempty JSON export no larger than 32 MB.');return}
    const next=new globalThis.FileReader();reader.current=next;setReading(true)
    next.onload=()=>{
      if(reader.current!==next)return
      reader.current=null
      setReading(false)
      try{setPending(parseConversationImport(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(next.result))))}
      catch(failure){setError(failure instanceof SyntaxError || failure instanceof TypeError ? 'The file is not a valid UTF-8 JSON export.' : failure.message)}
    }
    next.onerror=()=>{reader.current=null;setReading(false);setError('The export could not be read. Choose the file again.')}
    next.readAsArrayBuffer(file)
  }
  function confirm(){
    if(disabled || !pending)return
    try{onImport(pending);close()}
    catch{setError('The conversation could not be saved. Existing browser history has been preserved.')}
  }
  return <>
    <button ref={trigger} type="button" disabled={disabled || reading} onClick={()=>field.current.click()}>Import conversation</button>
    <input ref={field} type="file" accept=".json,application/json" hidden aria-label="Choose conversation export" onChange={choose}/>
    <dialog ref={dialog} className="chat-delete-dialog" aria-label="Import conversation" onCancel={event=>{event.preventDefault();reader.current?.abort();setReading(false);close()}}>
      <h3>Import conversation</h3>
      {reading && <p role="status">Reading local export…</p>}
      {error && <p role="alert">{error}</p>}
      {pending && <><p>{pending.messages.length} messages{pending.draft ? ' and an unsent draft' : ''}</p><p>This creates a new local conversation. Only message text, terminal reply status and the draft are imported. Tasks are not resumed; workspace files and preview permissions are not imported.</p></>}
      {disabled && <p>Finish or stop the active task before importing.</p>}
      <footer><button type="button" onClick={()=>{reader.current?.abort();setReading(false);close()}}>Cancel import</button><button type="button" disabled={disabled || !pending || reading} onClick={confirm}>Import as new conversation</button></footer>
    </dialog>
  </>
}
