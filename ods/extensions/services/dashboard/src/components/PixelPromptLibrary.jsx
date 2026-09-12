import {useEffect, useRef, useState} from 'react'
import { Bookmark } from 'lucide-react'
import PixelPromptTransfer from './PixelPromptTransfer'
import {readSavedPrompts, writeSavedPrompt} from '../lib/pixelSavedPrompts'

export default function PixelPromptLibrary({input, disabled, onInsert}) {
  const dialog = useRef(null), trigger = useRef(null), previous = useRef(null)
  const [items, setItems] = useState([])
  const [editing, setEditing] = useState(null)
  const [removing, setRemoving] = useState(null)
  const [error, setError] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  function refresh() {
    try {setItems(readSavedPrompts()); setError('')}
    catch {setError('Saved prompts could not be read. Existing browser data has been preserved.')}
  }
  useEffect(() => {
    const update = () => {if (dialog.current?.open) refresh()}
    window.addEventListener('storage', update)
    return () => window.removeEventListener('storage', update)
  }, [])
  function close() {dialog.current?.close(); setIsOpen(false); setEditing(null); setRemoving(null); trigger.current?.focus()}
  function edit(item) {previous.current = item; setEditing(item || {id:'prompt-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2, 10), title:'', text:input}); setError('')}
  function save(event) {
    event.preventDefault()
    try {setItems(writeSavedPrompt({...editing, title:editing.title.trim()}, previous.current)); setEditing(null); setError('')}
    catch (failure) {setError(failure.message)}
  }
  function remove() {
    try {setItems(writeSavedPrompt(removing, removing, true)); setRemoving(null); setError('')}
    catch (failure) {setError(failure.message)}
  }
  const fieldClass = 'my-2 block w-full rounded border border-theme-border bg-theme-bg p-2 text-theme-text'
  const buttonClass = 'rounded border border-theme-border px-3 py-2 text-xs hover:bg-theme-surface-hover disabled:opacity-40'
  return <>
    <button ref={trigger} type="button" disabled={disabled} aria-label="Saved prompts" title="Saved prompts" onClick={() => {refresh(); setIsOpen(true); dialog.current.showModal()}}><Bookmark size={16}/></button>
    <dialog ref={dialog} className="chat-delete-dialog" style={{maxHeight:'calc(100dvh - 32px)', overflowY:'auto'}} aria-label="Saved prompts" onCancel={event => {event.preventDefault(); close()}}>
      <h3>Saved prompts</h3><p>Reusable text stored in this browser. Insert a prompt into your draft, then review it before sending.</p>
      {error && <p role="alert">{error}</p>}
      {editing ? <form onSubmit={save}>
        <label>Prompt name<input autoFocus className={fieldClass} maxLength={80} value={editing.title} onChange={event => setEditing({...editing, title:event.target.value})}/></label>
        <label>Prompt text<textarea className={fieldClass} rows={6} maxLength={16000} value={editing.text} onChange={event => setEditing({...editing, text:event.target.value})}/></label>
        <footer><button type="button" onClick={() => {setEditing(null); setError('')}}>Cancel edit</button><button type="submit">Save prompt</button></footer>
      </form> : removing ? <div>
        <p>Delete saved prompt “{removing.title}”? Existing conversations are kept.</p>
        <footer><button type="button" autoFocus onClick={() => setRemoving(null)}>Keep prompt</button><button type="button" onClick={remove}>Delete prompt</button></footer>
      </div> : <>
        <button className={buttonClass} type="button" onClick={() => edit(null)}>Save a new prompt</button>
        {isOpen && <PixelPromptTransfer onImported={setItems}/>}
        {!items.length && <p>No saved prompts yet. Start with your current draft or write a new one.</p>}
        <ul>{items.map(item => {
          const fits = input.length + item.text.length + 1 <= 16384
          return <li key={item.id} className="my-3 rounded border border-theme-border p-2">
            <strong>{item.title}</strong><p className="whitespace-pre-wrap break-words">{item.text.slice(0, 160)}{item.text.length > 160 ? '…' : ''}</p>
            <div className="mt-2 flex flex-wrap gap-2">
            <button className={buttonClass} type="button" disabled={disabled || !fits} aria-label={`Insert prompt: ${item.title}`} onClick={() => {onInsert(item.text); close()}}>Insert</button>
            <button className={buttonClass} type="button" aria-label={`Edit prompt: ${item.title}`} onClick={() => edit(item)}>Edit</button>
            <button className={buttonClass} type="button" aria-label={`Delete prompt: ${item.title}`} onClick={() => {setRemoving(item); setError('')}}>Delete</button>
            </div>
            {!fits && <p>Shorten the current draft before inserting this prompt.</p>}
          </li>
        })}</ul>
      </>}
      <footer><button type="button" onClick={close}>Close prompts</button></footer>
    </dialog>
  </>
}
