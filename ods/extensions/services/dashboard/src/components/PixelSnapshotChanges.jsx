import {useEffect, useState} from 'react'
import PixelFileChanges, {PixelChangeCounts} from './PixelFileChanges'
import {Globe2,Files,ChevronDown} from 'lucide-react'
import {isArtifactPath, isSnapshotId, readBoundedBytes} from '../lib/pixelArtifacts'

export function validateSnapshotChanges(value, preview, before) {
  if (!isSnapshotId(preview?.siteId) || (before && !isSnapshotId(before.siteId))
    || value?.schemaVersion !== 1 || value.scope !== 'published-snapshots'
    || value.siteId !== preview.siteId || value.sha256 !== preview.sha256
    || value.beforeSiteId !== (before?.siteId ?? null) || value.beforeSha256 !== (before?.sha256 ?? null)
    || !Array.isArray(value.changes) || value.changes.length > 256) throw new Error('Unverified changes')
  const seen = new Set()
  const number = v => Number.isInteger(v) && v >= 0 && v <= 4000
  const line = v => v === null || number(v) && v > 0
  let rows = 0
  for (const file of value.changes) {
    if (!isArtifactPath(file?.path) || seen.has(file.path)
      || !(before ? ['created','modified','deleted'] : ['published']).includes(file.change)
      || (file.additions !== null && !number(file.additions)) || (file.deletions !== null && !number(file.deletions))
      || typeof file.truncated !== 'boolean' || !Array.isArray(file.diff)) throw new Error('Invalid change')
    seen.add(file.path)
    for (const row of file.diff) {
      rows++
      if (rows > 5000 || !['context','add','remove'].includes(row?.type) || typeof row.text !== 'string'
        || /[\r\n]/.test(row.text) || row.text.includes('\0') || !line(row.oldLine) || !line(row.newLine)
        || (row.type === 'add' ? row.oldLine !== null || row.newLine === null : row.type === 'remove' ? row.newLine !== null || row.oldLine === null : row.oldLine === null || row.newLine === null)) throw new Error('Invalid diff')
    }
  }
  return value.changes
}

export default function PixelSnapshotChanges({preview, rootPath=preview?.relativeDirectory, before = null, projectFiles, onPreview, variant='review', onReview, selectedPath, onSelectFile, onOpenFile}) {
  const stateKey = `${preview?.siteId}/${preview?.sha256}/${before?.siteId}/${before?.sha256}`
  const [snapshot, setState] = useState({status:'loading'})
  const state = snapshot.key === stateKey ? snapshot : {status:'loading'}
  const [retry, setRetry] = useState(0)
  const [all,setAll]=useState(false)
  useEffect(()=>{
    const abort = new AbortController()
    let disposed = false
    const timer = setTimeout(()=>abort.abort(),15000)
    setState({key:stateKey,status:'loading'})
    ;(async()=>{
      try {
        if (!isSnapshotId(preview?.siteId) || before && !isSnapshotId(before.siteId)) throw new Error()
        const response = await fetch(`/pixel-preview/${preview.siteId}/__ods_changes__/${before?.siteId || 'initial'}.json`,{signal:abort.signal,cache:'no-store'})
        const bytes = await readBoundedBytes(response,512*1024)
        const files = validateSnapshotChanges(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)),preview,before)
        if (!disposed) setState({key:stateKey,status:'ready',files})
      } catch { if (!disposed) setState({key:stateKey,status:'error'}) }
      finally { clearTimeout(timer) }
    })()
    return ()=>{disposed=true;clearTimeout(timer);abort.abort()}
  },[preview?.siteId,preview?.sha256,before?.siteId,before?.sha256,retry])
  const manifestPaths = Array.isArray(projectFiles) ? new Set(projectFiles.map(file=>file.path)) : null
  const inconsistent = state.status === 'ready' && manifestPaths && state.files.some(file => file.change === 'deleted' ? manifestPaths.has(file.path) : !manifestPaths.has(file.path))
  const status = state.status === 'loading' ? <p className="pixel-diff-status" role="status">Comparing published files…</p> : state.status === 'error' || inconsistent ? <p className="pixel-diff-status" role="status">File comparison unavailable. <button type="button" onClick={()=>setRetry(v=>v+1)}>Retry</button></p> : null
  if (status && variant !== 'summary' && !manifestPaths) return status
  const counted = state.files?.every(file=>Number.isInteger(file.additions) && Number.isInteger(file.deletions))
  if(variant==='summary')return <div className="portal-artifact-cards">
    {onPreview && <button type="button" className="portal-preview-card" onClick={onPreview}><Globe2 size={20}/><span><strong>Web preview</strong><small>{preview.relativeDirectory}</small></span><span>Open ↗</span></button>}
    {status || <section className="portal-change-card" aria-label="Published file changes">
      <header><Files size={19}/><div><strong>{state.files.length} {state.files.length===1?'changed file':'changed files'}</strong>{counted && <PixelChangeCounts additions={state.files.reduce((n,f)=>n+f.additions,0)} deletions={state.files.reduce((n,f)=>n+f.deletions,0)}/>}</div>{onReview && <button type="button" onClick={()=>onReview(null)}>Review</button>}</header>
      <div>{(all?state.files:state.files.slice(0,5)).map(file=><button type="button" key={file.path} className="portal-change-file" onClick={()=>onReview?.(file.path)} disabled={!onReview}><span title={file.path}>{file.path}</span><PixelChangeCounts additions={file.additions} deletions={file.deletions}/></button>)}</div>
      {state.files.length>5 && <button className="portal-change-more" type="button" onClick={()=>setAll(value=>!value)}>{all?'Show fewer files':`Show all ${state.files.length} files`}<ChevronDown size={13}/></button>}
    </section>}
  </div>
  return <section className="pixel-snapshot-changes portal-snapshot-review" aria-label="Published file changes">
    {status}
    {!status && !state.files.length && !manifestPaths && <p className="pixel-diff-status" role="status">No changes between published versions</p>}
    <PixelFileChanges changes={status ? [] : state.files} files={projectFiles} comparisonReady={!status} rootPath={rootPath} onPreview={onPreview} selectedPath={selectedPath} onSelectFile={onSelectFile} onOpenFile={onOpenFile}/>
  </section>
}
