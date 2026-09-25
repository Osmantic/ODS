import { useEffect, useRef, useState } from 'react'
import { conversationLabels, saveConversationLabels } from '../lib/pixelConversationLabels'
import './pixel-conversation-organizer.css'

export default function PixelConversationOrganizer({chat, title, onSaved}) {
  const dialog = useRef(null)
  const trigger = useRef(null)
  const original = useRef(null)
  const [editing, setEditing] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { if (editing) dialog.current?.showModal() }, [Boolean(editing)])
  function close() { dialog.current?.close(); setEditing(null); setError(''); trigger.current?.focus() }
  function save(event) {
    event.preventDefault()
    try { saveConversationLabels(chat.chatId, editing, original.current); close(); onSaved?.() }
    catch (failure) { setError(failure.message || 'Labels could not be saved. Your conversation is unchanged.') }
  }
  return <>
    <button ref={trigger} type="button" className="conversation-organize" aria-label={`Organize chat: ${title}`} title="Rename, pin or archive" onClick={() => {setError(''); original.current = conversationLabels(chat.chatId); setEditing(original.current)}}>…</button>
    {editing && <dialog ref={dialog} className="chat-delete-dialog" aria-label="Organize conversation" onCancel={event => {event.preventDefault(); close()}}>
      <form onSubmit={save}>
        <h3>Organize conversation</h3>
        <label>Conversation name<input className="mt-2 block w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text" autoFocus maxLength={80} value={editing.title} placeholder="Use the first message" onChange={event => setEditing({...editing, title:event.target.value})}/></label>
        <p>Leave the name empty to use the first message. Labels are saved in this browser.</p>
        <label className="my-2 flex items-center gap-2"><input type="checkbox" checked={editing.pinned} onChange={event => setEditing({...editing, pinned:event.target.checked})}/>Pin conversation</label>
        <label className="my-2 flex items-center gap-2"><input type="checkbox" checked={editing.archived} onChange={event => setEditing({...editing, archived:event.target.checked})}/>Archive conversation</label>
        <p>Archiving hides it from the main list. It keeps all messages and does not stop running work.</p>
        {error && <p role="alert">{error}</p>}
        <footer><button type="button" onClick={close}>Cancel</button><button type="submit">Save labels</button></footer>
      </form>
    </dialog>}
  </>
}
