import {useEffect, useRef, useState} from 'react'
import {mergePromptBackup, parsePromptBackup, promptBackupText} from '../lib/pixelPromptTransfer'

export default function PixelPromptTransfer({onImported}) {
  const reader = useRef(null)
  const [pending, setPending] = useState(null)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => () => { if (reader.current) {reader.current.onload = null;reader.current.onerror = null;reader.current.abort()} }, [])
  function select(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setPending(null);setError('');setNotice('')
    if (file.size > 4 * 1024 * 1024) {setError('Choose a JSON backup no larger than 4 MB.');return}
    setReading(true)
    const current = new globalThis.FileReader()
    reader.current = current
    current.onload = () => {
      setReading(false)
      try {setPending(parsePromptBackup(current.result))}
      catch (failure) {setError(failure instanceof SyntaxError ? 'This file is not valid JSON.' : failure.message)}
    }
    current.onerror = () => {setReading(false);setError('This backup could not be read. Try selecting it again.')}
    current.readAsText(file)
  }
  function restore() {
    try {
      const items = mergePromptBackup(pending)
      onImported(items)
      setPending(null);setError('');setNotice('Backup imported. Existing prompts were kept; exact duplicates were skipped.')
    } catch (failure) {setError(failure.message)}
  }
  function download() {
    setError('')
    try {
      const url = URL.createObjectURL(new Blob([promptBackupText()],{type:'application/json'}))
      const link = document.createElement('a')
      link.href=url;link.download='pixel-saved-prompts.json';document.body.append(link)
      try {link.click()} finally {link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
    } catch {setError('The prompt backup could not be downloaded. Existing browser data was preserved.')}
  }
  return <div className="my-3 space-y-2 rounded border border-theme-border p-2 text-xs">
    <button type="button" onClick={download}>Download prompt backup</button>
    <label className="block">Import prompt backup<input className="mt-1 block max-w-full" type="file" accept=".json,application/json" disabled={reading} onChange={select}/></label>
    {reading && <p role="status">Reading backup…</p>}
    {pending && <div>
      <p>Review {pending.length} prompts before adding them. Matching names with different text are kept as separate prompts.</p>
      <ul className="max-h-32 overflow-y-auto">{pending.map((item,index)=><li key={index}>{item.title}</li>)}</ul>
      <div className="mt-2 flex gap-3"><button type="button" onClick={()=>{setPending(null);setError('')}}>Cancel import</button><button type="button" onClick={restore}>Confirm prompt import</button></div>
    </div>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </div>
}
