import {useEffect, useRef, useState} from 'react'
import {exportSavedPrompts, MAX_PROMPT_BACKUP_BYTES, parseSavedPromptBackup, restoreSavedPrompts} from '../lib/pixelSavedPrompts'

const button = 'rounded border border-theme-border px-3 py-2 text-xs hover:bg-theme-surface-hover disabled:opacity-40'

export default function PixelPromptBackup({onRestored}) {
  const [preview, setPreview] = useState(null)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const sequence = useRef(0)
  const field = useRef(null)
  useEffect(() => () => {sequence.current++}, [])
  function clear() {sequence.current++; setPreview(null); setReading(false); setError('')}
  function download() {
    setError(''); setNotice('')
    try {
      const url = URL.createObjectURL(new Blob([exportSavedPrompts()], {type:'application/json'}))
      const anchor = document.createElement('a')
      try {
        anchor.href = url; anchor.download = 'ods-saved-prompts.json'
        document.body.appendChild(anchor); anchor.click()
      } finally {anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)}
    } catch {setError('The backup could not be created. Existing saved prompts are unchanged.')}
  }
  async function choose(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    clear(); setNotice('')
    const current = sequence.current
    if (!file.size || file.size > MAX_PROMPT_BACKUP_BYTES) {setError('Choose a nonempty prompt backup no larger than 3 MB.'); return}
    setReading(true)
    try {
      const raw = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer())
      const prompts = parseSavedPromptBackup(raw)
      if (current === sequence.current) setPreview({raw, prompts})
    } catch {if (current === sequence.current) setError('This file is not a valid ODS saved-prompt backup.')}
    finally {if (current === sequence.current) setReading(false)}
  }
  function restore() {
    if (!preview || reading) return
    try {
      const result = restoreSavedPrompts(preview.raw)
      onRestored(result.items)
      setPreview(null); setError('')
      setNotice(`Imported ${result.added} prompts; skipped ${result.skipped} matching prompts.`)
    } catch (failure) {setError(failure instanceof RangeError ? failure.message : 'The import could not be saved. Check browser storage and try again. Existing prompts were preserved.')}
  }
  return <section aria-label="Prompt backups" className="my-3 rounded border border-theme-border p-3">
    <div className="flex flex-wrap gap-3">
      <button className={button} type="button" onClick={download}>Export prompts</button>
      <button className={button} type="button" disabled={reading} onClick={() => field.current?.click()}>Import prompt backup</button>
      <input ref={field} hidden type="file" accept=".json,application/json" aria-label="Choose prompt backup" onChange={choose}/>
    </div>
    <p className="mt-2 text-xs">Backups contain your saved prompt names and text. Keep the file private if your prompts contain sensitive information.</p>
    {reading && <p role="status">Reading prompt backup… <button type="button" onClick={clear}>Cancel import</button></p>}
    {preview && <div>
      <p>Review {preview.prompts.length} prompts. Import adds new text and skips exact name/text matches. Existing prompts are kept, including those with the same name. The library limit remains 30.</p>
      <ul>{preview.prompts.map(item => <li key={item.id}><details><summary className="break-words">{item.title}</summary><p className="whitespace-pre-wrap break-words">{item.text}</p></details></li>)}</ul>
      <p>Imported text is not inserted into your draft or sent to a model.</p>
      <div className="mt-2 flex flex-wrap gap-3"><button className={button} type="button" onClick={clear}>Cancel import</button><button className={button} type="button" onClick={restore}>Import reviewed prompts</button></div>
    </div>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
