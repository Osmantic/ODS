import { useEffect, useState } from 'react'
import { ArrowLeft, Search } from 'lucide-react'
import { loadSnapshotFiles } from '../lib/pixelArtifacts'
import PixelPreviewSource from './PixelPreviewSource'
import PixelPublicationDownload from './PixelPublicationDownload'
import { PixelLanguageBadge } from './PixelCodeBlock'

const fileType = file => {
  const name = file.path.split('/').at(-1)
  return name.includes('.') ? name.split('.').at(-1).toLowerCase() : '(no extension)'
}

export default function PixelTaskFiles({preview}) {
  const [files, setFiles] = useState(null)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState(null)
  const [type, setType] = useState('')
  const [sort, setSort] = useState('published')
  useEffect(() => {
    let current = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 12000)
    setFiles(null); setError(false); setSelected(null)
    loadSnapshotFiles(preview, controller.signal).then(value => {
      if (current) setFiles(value)
    }).catch(() => { if (current) setError(true) }).finally(() => clearTimeout(timer))
    return () => { current = false; controller.abort(); clearTimeout(timer) }
  }, [preview.siteId, preview.sha256, preview.entrySha256, preview.bytes, preview.files, attempt])
  const types = [...new Set([...(files || []).map(fileType), type].filter(Boolean))].sort()
  const shown = files?.filter(file => file.path.toLowerCase().includes(filter.toLowerCase()) && (!type || fileType(file) === type)) || []
  if (sort !== 'published') shown.sort((a, b) => {
    const names = a.path.localeCompare(b.path, undefined, {numeric:true})
    if (sort === 'largest') return b.bytes - a.bytes || names
    if (sort === 'smallest') return a.bytes - b.bytes || names
    return sort === 'reverse-name' ? -names : names
  })
  return <section className="pixel-task-files pixel-original-files" aria-label="Task files">
    <div className="pixel-file-toolbar"><Search size={14}/><input type="search" aria-label="Filter task files" placeholder="Find a file…" value={filter} onChange={event => setFilter(event.target.value)}/></div>
    <div className="flex flex-wrap items-center gap-2 px-3 pb-2 text-xs">
      <label>Type <select className="rounded border border-theme-border bg-theme-bg p-1" aria-label="File type" value={type} onChange={event => setType(event.target.value)}><option value="">All types</option>{types.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>Sort <select className="rounded border border-theme-border bg-theme-bg p-1" aria-label="Sort task files" value={sort} onChange={event => setSort(event.target.value)}><option value="published">Published order</option><option value="name">Name A–Z</option><option value="reverse-name">Name Z–A</option><option value="largest">Largest first</option><option value="smallest">Smallest first</option></select></label>
      {(filter || type) && <button type="button" onClick={() => {setFilter(''); setType('')}}>Clear file filters</button>}
    </div>
    <p className="pixel-file-caption">{preview.relativeDirectory}<span>{files ? `${files.length} published files` : 'Published files'}</span></p>
    {error ? <div role="alert" className="pixel-file-empty"><p>Task files could not be verified.</p><button type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>
      : !files ? <p role="status" className="pixel-file-empty">Verifying published files…</p>
        : <>
          <PixelPublicationDownload key={`${preview.siteId}/${preview.sha256}`} preview={preview}/>
          <p className="pixel-file-caption" role="status">Showing {shown.length} of {files.length} files · {shown.reduce((total, file) => total + file.bytes, 0).toLocaleString()} bytes</p>
          <ul className="pixel-task-file-list">{shown.map(file => {
            return <li key={file.path}><button type="button" aria-current={selected?.path === file.path ? 'true' : undefined} onClick={() => setSelected(file)}><PixelLanguageBadge path={file.path}/><span>{file.path}</span><small>{file.bytes < 1024 ? `${file.bytes} B` : `${(file.bytes / 1024).toFixed(1)} KB`}</small></button></li>
          })}</ul>
          {!shown.length && <p className="pixel-file-empty">No files match your search.</p>}
          {selected && <><button className="pixel-file-back" type="button" onClick={() => setSelected(null)}><ArrowLeft size={14}/>All files</button><PixelPreviewSource key={`${preview.siteId}/${selected.path}`} preview={preview} file={selected}/></>}
          <p className="pixel-file-footnote">These are the files in this published preview. Unpublished workspace files are not listed.</p>
        </>}
  </section>
}
