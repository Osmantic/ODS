import {useEffect, useState} from 'react'
import PixelFileChanges, {PixelChangeCounts} from './PixelFileChanges'
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

export default function PixelSnapshotChanges({preview, before = null, onPreview}) {
  const [state, setState] = useState({status:'loading'})
  const [retry, setRetry] = useState(0)
  useEffect(()=>{
    const abort = new AbortController()
    let disposed = false
    const timer = setTimeout(()=>abort.abort(),15000)
    setState({status:'loading'})
    ;(async()=>{
      try {
        if (!isSnapshotId(preview?.siteId) || before && !isSnapshotId(before.siteId)) throw new Error()
        const response = await fetch(`/pixel-preview/${preview.siteId}/__ods_changes__/${before?.siteId || 'initial'}.json`,{signal:abort.signal,cache:'no-store'})
        const bytes = await readBoundedBytes(response,512*1024)
        const files = validateSnapshotChanges(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)),preview,before)
        if (!disposed) setState({status:'ready',files})
      } catch { if (!disposed) setState({status:'error'}) }
      finally { clearTimeout(timer) }
    })()
    return ()=>{disposed=true;clearTimeout(timer);abort.abort()}
  },[preview?.siteId,preview?.sha256,before?.siteId,before?.sha256,retry])
  if (state.status === 'loading') return <p className="pixel-diff-status" role="status">Comparing published files…</p>
  if (state.status === 'error') return <p className="pixel-diff-status" role="status">File comparison unavailable. <button type="button" onClick={()=>setRetry(v=>v+1)}>Retry</button></p>
  const counted = state.files.every(file=>Number.isInteger(file.additions) && Number.isInteger(file.deletions))
  return <section className="pixel-snapshot-changes" aria-label="Published file changes">
    <div className="pixel-diff-summary"><span>{state.files.length ? `${state.files.length} changed ${state.files.length === 1 ? 'file' : 'files'}` : 'No changes between published versions'}</span>
      {counted && <PixelChangeCounts additions={state.files.reduce((n,f)=>n+f.additions,0)} deletions={state.files.reduce((n,f)=>n+f.deletions,0)}/>}</div>
    {!before && <p className="pixel-diff-status">First published version · compared with an empty publication.</p>}
    <PixelFileChanges changes={state.files} onPreview={onPreview}/>
  </section>
}
