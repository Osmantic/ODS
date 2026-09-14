import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, X, MessageSquare, Plus, Settings, ListChecks, Globe2 } from 'lucide-react'
import { readConversations, conversationTitle, SELECT_EVENT, LIBRARY_EVENT } from '../lib/pixelConversations'
import { conversationExcerpt } from '../lib/pixelConversationSearch'

export const OPEN_PIXEL_SEARCH = 'ods:pixel-search'
export default function PixelCommandSearch({ onInsert, onNewTask }) {
  const navigate = useNavigate()
  const dialog = useRef(null)
  const field = useRef(null)
  const trigger = useRef(null)
  const [query, setQuery] = useState('')
  const [chats, setChats] = useState([])
  const [index, setIndex] = useState(0)
  useEffect(() => {
    const open = () => {
      if (dialog.current.open) return
      trigger.current = document.activeElement
      setQuery(''); setIndex(0); setChats(readConversations())
      if (!dialog.current.open) dialog.current.showModal()
      field.current?.focus()
    }
    const key = event => {
      if (event.isComposing) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); open() }
    }
    const refresh = () => {
      if (!dialog.current?.open) return
      setChats(readConversations()); setIndex(0)
    }
    window.addEventListener(OPEN_PIXEL_SEARCH, open)
    window.addEventListener('keydown', key)
    window.addEventListener(LIBRARY_EVENT, refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(OPEN_PIXEL_SEARCH, open); window.removeEventListener('keydown', key)
      window.removeEventListener(LIBRARY_EVENT, refresh); window.removeEventListener('storage', refresh)
    }
  }, [])
  const entries = [
    { title: 'New task', detail: 'Start a fresh conversation', icon: Plus, run: onNewTask },
    { title: 'Pixel settings', detail: 'Connections, access and sharing', icon: Settings, run: () => navigate('/pixel/settings') },
    { title: 'Plan deep work', detail: 'Draft milestones and completion checks', icon: ListChecks, run: () => onInsert('Build a durable plan for this goal, then show the milestones and exact completion criteria before work begins.') },
    { title: 'Research with evidence', detail: 'Draft a research request with citations', icon: Globe2, run: () => onInsert('Research this question using current sources, inline citations, and explicit evidence-versus-inference labels.') },
    ...chats.map(chat => ({ title: conversationTitle(chat), detail: `${chat.messages.filter(item => item.role === 'user').length} turns · Saved locally`, excerpt:conversationExcerpt(chat, query), icon: MessageSquare, run: () => window.dispatchEvent(new CustomEvent(SELECT_EVENT, { detail: chat.chatId })) })),
  ].filter(item => item.excerpt || `${item.title} ${item.detail}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  function close() { dialog.current.close(); trigger.current?.focus?.() }
  function choose(entry) { if (entry) { close(); entry.run() } }
  return <dialog ref={dialog} className="pixel-command-dialog" aria-label="Search Pixel" onClick={event => { if (event.target === event.currentTarget) close() }} onCancel={() => trigger.current?.focus?.()}>
    <div className="pixel-command-input"><Search size={17}/><input ref={field} aria-label="Search conversations and actions" placeholder="Search conversations and actions…" value={query} onChange={event => { setQuery(event.target.value); setIndex(0) }} onKeyDown={event => {
      if (event.nativeEvent.isComposing) return
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setIndex(value => entries.length ? (value + (event.key === 'ArrowDown' ? 1 : -1) + entries.length) % entries.length : 0) }
      if (event.key === 'Enter') { event.preventDefault(); choose(entries[index]) }
    }}/><button aria-label="Close search" onClick={close}><X size={16}/></button></div>
    <div className="pixel-command-results">{entries.length ? entries.map((entry, at) => <button className={at === index ? 'is-active' : ''} key={`${entry.title}-${at}`} onFocus={() => setIndex(at)} onClick={() => choose(entry)}><entry.icon size={17}/><span><strong>{entry.title}</strong><small>{entry.detail}</small>{entry.excerpt && <small className="break-words whitespace-normal">{entry.excerpt}</small>}</span></button>) : <p>No matching conversations or actions.</p>}</div>
  </dialog>
}
