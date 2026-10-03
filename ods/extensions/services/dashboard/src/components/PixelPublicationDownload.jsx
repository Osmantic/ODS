import {useEffect,useRef,useState} from 'react'
import {zipSync} from 'fflate'
import {loadSnapshotFiles,loadArtifactBytes} from '../lib/pixelArtifacts'

export default function PixelPublicationDownload({preview}) {
  const pending=useRef(null)
  const [state,setState]=useState({status:'idle'})
  useEffect(()=>()=>{pending.current?.abort();pending.current=null},[])
  function cancel() {
    pending.current?.abort();pending.current=null
    setState({status:'idle'})
  }
  async function download() {
    if(pending.current)return
    const controller=new AbortController()
    pending.current=controller
    setState({status:'working',done:0})
    let rejectAbort
    const aborted=new Promise((_,reject)=>{
      rejectAbort=()=>reject(new Error('Download cancelled or timed out'))
      controller.signal.addEventListener('abort',rejectAbort,{once:true})
    })
    const timer=setTimeout(()=>controller.abort(),30000)
    try {
      const collect=async()=>{
        const files=await loadSnapshotFiles(preview,controller.signal)
        const entries=Object.create(null)
        for(const file of files){
          controller.signal.throwIfAborted()
          const bytes=await loadArtifactBytes(preview,file,controller.signal)
          controller.signal.throwIfAborted()
          entries[file.path]=new Uint8Array(bytes)
          if(pending.current===controller)setState({status:'working',done:Object.keys(entries).length,total:files.length})
        }
        // The manifest contract caps input at 128 files / 16 MiB. Store ZIP
        // entries without compression so packaging has bounded CPU cost.
        return zipSync(entries,{level:0})
      }
      const bytes=await Promise.race([collect(),aborted])
      if(pending.current!==controller)return
      const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}))
      const anchor=document.createElement('a')
      try {
        anchor.href=url;anchor.download=`ods-${preview.siteId}.zip`
        document.body.append(anchor);anchor.click()
      } finally {anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
      setState({status:'done'})
    } catch {
      if(pending.current===controller)setState({status:'error'})
    } finally {
      clearTimeout(timer)
      controller.signal.removeEventListener('abort',rejectAbort)
      if(pending.current===controller)pending.current=null
    }
  }
  return <div className="px-3 pb-2 text-xs">
    <button type="button" disabled={state.status==='working'} onClick={download}>Download publication ZIP</button>
    {state.status==='working' && <><span role="status"> Verifying files {state.done}{state.total ? ` of ${state.total}` : ''}…</span> <button type="button" onClick={cancel}>Cancel ZIP download</button></>}
    {state.status==='done' && <p role="status">Publication download started.</p>}
    {state.status==='error' && <p role="alert">The complete publication could not be verified or downloaded. No partial archive was saved. Try again.</p>}
  </div>
}
