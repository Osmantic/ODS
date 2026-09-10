import { useEffect, useRef, useState } from 'react'
import { Plus } from 'lucide-react'
import { CHAT_KEY, LIBRARY_EVENT, SELECT_EVENT, DELETE_EVENT, readConversations, conversationTitle } from '../lib/pixelConversations'
import { conversationLabels } from '../lib/pixelConversationLabels'
import PixelConversationRow, {ConversationTitle} from './PixelConversationRow'

import { exportConversation } from '../lib/pixelConversationExport'
import { forkConversation } from '../lib/pixelConversationFork'

export default function PixelConversationNavigation({ collapsed }) {
  const [chats, setChats] = useState(readConversations)
  const [active, setActive] = useState('')
  const [pending, setPending] = useState(null)
  const [deleteError, setDeleteError] = useState('')
  const [exportError, setExportError] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const archiveToggle = useRef(null)

  const dialog = useRef(null)
  const trigger = useRef(null)
  useEffect(() => { if (pending) dialog.current?.showModal() }, [pending])
  function closeDelete() { dialog.current?.close(); setPending(null); setDeleteError(''); trigger.current?.focus() }
  function confirmDelete() {
    window.dispatchEvent(new CustomEvent(DELETE_EVENT, {detail:{chatId:pending.chatId, complete:error => {
      if (error) setDeleteError(error)
      else closeDelete()
    }}}))
  }
  useEffect(() => {
    const refresh = () => {
      setChats(readConversations())
      try { setActive(JSON.parse(localStorage.getItem(CHAT_KEY) || 'null')?.chatId || '') } catch { setActive('') }
    }
    refresh()
    window.addEventListener(LIBRARY_EVENT, refresh)
    window.addEventListener('storage', refresh)
    return () => { window.removeEventListener(LIBRARY_EVENT, refresh); window.removeEventListener('storage', refresh) }
  }, [])
  if (collapsed) return <button className="pixel-nav-item" aria-label="New task" title="New task" onClick={() => window.dispatchEvent(new Event('ods:pixel-new-task'))}><Plus size={16}/></button>
  const labeled = chats.map(chat => ({chat, labels:conversationLabels(chat.chatId)}))
  const visible = labeled.filter(item => item.labels.archived === showArchived)
  const pinned = visible.filter(item => item.labels.pinned).map(item => item.chat)
  const regular = visible.filter(item => !item.labels.pinned).map(item => item.chat)
  const chevron = <svg className="rail-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m5 6 3 3 3-3"/></svg>
  function rows(items, empty) {
    return <div className="rail-conversations">{items.length ? items.map(chat => <PixelConversationRow key={chat.chatId} chat={chat} title={conversationTitle(chat)} onSaved={() => archiveToggle.current?.focus()} onDelete={event => {trigger.current=event.currentTarget;setDeleteError('');setPending(chat)}} onFork={() => { try { forkConversation(chat.chatId) } catch { setExportError('Failed to duplicate chat.') } }} onExport={() => {
      try { exportConversation(chat.chatId); setExportError('') }
      catch { setExportError('This conversation could not be exported. Your saved history is unchanged.') }
    }}><button className={`conversation-link ${chat.chatId === active ? 'active' : ''}`} title={conversationTitle(chat)} aria-current={chat.chatId === active ? 'page' : undefined} onClick={() => window.dispatchEvent(new CustomEvent(SELECT_EVENT, { detail: chat.chatId }))}><ConversationTitle title={conversationTitle(chat)}/>{chat.inFlight && <span className="rail-task-running" role="status" aria-label="Working"/>}</button></PixelConversationRow>) : <span className="rail-empty">{empty}</span>}</div>

  }
  return <div className="pixel-conversation-navigation">
    {exportError && <p role="alert">{exportError}</p>}
    <dialog ref={dialog} className="chat-delete-dialog" aria-labelledby="delete-chat-title" onCancel={event => { event.preventDefault(); closeDelete() }}>
      <h3 id="delete-chat-title">Delete this chat?</h3>
      <p>{pending && conversationTitle(pending)}</p>
      <p>This removes the conversation from this browser. Workspace files and published previews are kept. This cannot be undone.</p>
      {deleteError && <p role="alert">{deleteError}</p>}
      <footer><button autoFocus onClick={closeDelete}>Cancel</button><button onClick={confirmDelete}>Delete chat</button></footer>
    </dialog>
    <button className="pixel-nav-item" onClick={() => window.dispatchEvent(new Event('ods:pixel-new-task'))}><Plus size={16}/><span>New task</span></button>
    <button ref={archiveToggle} className="pixel-nav-item" type="button" aria-pressed={showArchived} onClick={() => setShowArchived(value => !value)}>{showArchived ? 'Show active conversations' : `Archived (${labeled.filter(item => item.labels.archived).length})`}</button>
    <div className="pixel-original-sections">
      {pinned.length > 0 && <details className="rail-section" open><summary>Pinned{chevron}</summary>{rows(pinned, '')}</details>}
      <details className="rail-section" open>
        <summary>Projects{chevron}</summary>
        <details className="rail-project" open>
          <summary><svg className="rail-folder" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6V4.8A1.3 1.3 0 0 1 4.3 3.5h4l2 2h5.4A1.3 1.3 0 0 1 17 6.8V8M4 16.5h11.3a1.5 1.5 0 0 0 1.5-1.2L18 9.2A1 1 0 0 0 17 8H5.2a1.5 1.5 0 0 0-1.5 1.2L2.5 15A1.3 1.3 0 0 0 4 16.5Z"/></svg><span>Playground</span>{chevron}</summary>
          {rows(regular.slice(0, 5), showArchived ? 'No archived conversations' : 'No conversations yet')}
        </details>
      </details>
      <details className="rail-section" open><summary>Recent{chevron}</summary>{rows(regular.slice(5), 'No older conversations')}</details>
    </div>
  </div>
}
