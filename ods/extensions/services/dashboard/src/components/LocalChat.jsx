import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Plus, Send, Square, Trash2 } from 'lucide-react'
import PortalStreamingText from './PortalStreamingText'
import { usePixelSendKey, shouldSendMessage } from '../lib/usePixelSendKey'
import { streamLocalChat } from '../lib/localChatStream'
import {
  exportFileName, exportJson, exportMarkdown, loadConversations, newConversation, saveConversations, upsertConversation,
} from '../lib/localChatStore'

const STATUS_POLL_MS = 10000
const STATUS_TIMEOUT_MS = 5000
const STATE_TEXT = {
  model_loading: 'The model is still loading. Try again in a moment.',
  model_stopped: 'The local model is stopped. Start ODS from the Start menu to chat again.',
  model_failed: 'The local model could not start. Open ODS setup to repair it.',
  model_unavailable: 'The local model is not answering. Restart ODS and try again.',
}

function download(name, type, text) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

// Polls /api/local-chat/status one request at a time (no pile-up), bounded
// by a timeout, and stops polling once the model is ready.
function useLocalModelStatus() {
  const [status, setStatus] = useState({ state: 'checking' })
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    let timer = null
    const poll = async () => {
      const controller = new AbortController()
      const deadline = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)
      let next
      try {
        const response = await fetch('/api/local-chat/status', { signal: controller.signal, cache: 'no-store' })
        next = response.ok ? await response.json() : { state: 'model_unavailable' }
      } catch {
        next = { state: 'model_unavailable' }
      } finally {
        clearTimeout(deadline)
      }
      if (!active) return
      setStatus(next)
      if (next.state !== 'ready') timer = setTimeout(poll, STATUS_POLL_MS)
    }
    poll()
    return () => { active = false; clearTimeout(timer) }
  }, [refresh])
  return [status, () => setRefresh(value => value + 1)]
}

export default function LocalChat() {
  const [conversations, setConversations] = useState(() => loadConversations())
  const [current, setCurrent] = useState(() => conversations[0] || newConversation())
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState(null)
  const [dropped, setDropped] = useState(0)
  const [storageFailed, setStorageFailed] = useState(false)
  const [status, recheck] = useLocalModelStatus()
  const sendKey = usePixelSendKey()
  const abortRef = useRef(null)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView?.({ block: 'end' }) }, [current.messages])
  useEffect(() => () => abortRef.current?.abort(), [])

  const conversationsRef = useRef(conversations)
  const persist = useCallback(conversation => {
    // Saved outside a state updater: React may run updaters twice.
    const result = saveConversations(upsertConversation(conversationsRef.current, conversation))
    conversationsRef.current = result.conversations
    setStorageFailed(!result.ok)
    setConversations(result.conversations)
    setCurrent(conversation)
  }, [])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming) return
    setError(null)
    const asked = { ...current, messages: [...current.messages, { role: 'user', content: text }] }
    const history = asked.messages
    let answer = ''
    setInput('')
    setStreaming(true)
    setCurrent({ ...asked, messages: [...history, { role: 'assistant', content: '' }] })
    const controller = new AbortController()
    abortRef.current = controller
    let contextStart = asked.contextStart
    let failure = null
    try {
      const result = await streamLocalChat({
        messages: history, contextStart: asked.contextStart, signal: controller.signal,
        onContext: note => { contextStart = note.contextStart; setDropped(note.droppedMessages || 0) },
        onText: piece => {
          answer += piece
          setCurrent({ ...asked, messages: [...history, { role: 'assistant', content: answer }] })
        },
      })
      failure = result.error
    } catch (exc) {
      if (exc?.name !== 'AbortError') failure = { code: 'model_unavailable', message: STATE_TEXT.model_unavailable }
    } finally {
      abortRef.current = null
      setStreaming(false)
    }
    if (failure && !answer) {
      // Nothing came back: return the owner's words to the box, unsent.
      setCurrent(current)
      setInput(text)
      setError(failure.message)
      if (failure.code?.startsWith('model_')) recheck()
      return
    }
    if (failure) setError(failure.message)
    const stopped = controller.signal.aborted ? '\n\n_Stopped._' : ''
    persist({ ...asked, contextStart, messages: [...history, { role: 'assistant', content: answer + stopped }] })
  }, [current, input, persist, recheck, streaming])

  const startNew = () => {
    if (streaming) return
    setCurrent(newConversation())
    setDropped(0)
    setError(null)
  }

  const remove = () => {
    if (streaming) return
    const rest = conversations.filter(item => item.id !== current.id)
    const result = saveConversations(rest)
    conversationsRef.current = result.conversations
    setStorageFailed(!result.ok)
    setConversations(result.conversations)
    setCurrent(result.conversations[0] || newConversation())
    setDropped(0)
  }

  const ready = status.state === 'ready'
  const statusText = status.state === 'checking' ? 'Connecting to your local model…' : STATE_TEXT[status.state] || status.message
  return (
    <section className="pixel-chat flex flex-col h-full text-theme-text" aria-label="Local chat">
      <header className="flex flex-wrap items-center gap-2 border-b border-theme-border px-4 py-3">
        <h1 className="text-base font-semibold mr-auto">Local chat</h1>
        {ready && <span className="text-xs text-theme-text-muted">{status.model} · {status.contextSize?.toLocaleString()} tokens</span>}
        <label className="sr-only" htmlFor="local-chat-history">Saved chats</label>
        <select id="local-chat-history" className="max-w-[14rem] truncate rounded border border-theme-border bg-theme-bg px-2 py-1 text-sm"
          value={current.id} disabled={streaming}
          onChange={event => { setCurrent(conversations.find(item => item.id === event.target.value) || current); setDropped(0) }}>
          {!conversations.some(item => item.id === current.id) && <option value={current.id}>New chat</option>}
          {conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
        <button type="button" className="pixel-metal-control p-2" onClick={startNew} disabled={streaming} aria-label="New chat"><Plus size={16} /></button>
        <button type="button" className="pixel-metal-control p-2" disabled={!current.messages.length}
          onClick={() => download(`${exportFileName(current.title)}.md`, 'text/markdown', exportMarkdown(current))} aria-label="Save chat as Markdown"><Download size={16} /></button>
        <button type="button" className="pixel-metal-control p-2 text-xs" disabled={!current.messages.length}
          onClick={() => download(`${exportFileName(current.title)}.json`, 'application/json', exportJson(current))} aria-label="Save chat as JSON">JSON</button>
        <button type="button" className="pixel-metal-control p-2" onClick={remove} disabled={streaming || !conversations.some(item => item.id === current.id)} aria-label="Delete this chat"><Trash2 size={16} /></button>
      </header>

      {!ready && <p role="status" className="border-b border-theme-border px-4 py-2 text-sm text-theme-text-secondary">{statusText}</p>}
      {storageFailed && <p role="status" className="border-b border-theme-border px-4 py-2 text-xs text-theme-text-secondary">This browser is not saving chats right now (a private window or full storage). You can keep chatting.</p>}

      <div className="flex-1 overflow-y-auto px-4 py-4" role="log" aria-live="polite" aria-label="Messages">
        {!current.messages.length && <p className="text-theme-text-muted text-sm">Ask anything. Your chats stay on this PC.</p>}
        {current.messages.map((message, index) => (
          <article key={index} className="mb-4" aria-label={message.role === 'user' ? 'You' : 'ODS'}>
            <p className="text-xs font-semibold text-theme-text-muted mb-1">{message.role === 'user' ? 'You' : 'ODS'}</p>
            {message.role === 'user'
              ? <p className="whitespace-pre-wrap">{message.content}</p>
              : <PortalStreamingText active={streaming && index === current.messages.length - 1}>{message.content}</PortalStreamingText>}
          </article>
        ))}
        <div ref={endRef} />
      </div>

      {dropped > 0 && <p role="note" className="px-4 pt-2 text-xs text-theme-text-muted">The model sees the most recent part of this chat; {dropped} earlier {dropped === 1 ? 'message is' : 'messages are'} still saved here.</p>}
      {error && <p role="alert" className="px-4 pt-2 text-sm text-red-400">{error}</p>}

      <form className="flex gap-2 border-t border-theme-border p-4" onSubmit={event => { event.preventDefault(); send() }}>
        <label className="sr-only" htmlFor="local-chat-input">Message</label>
        <textarea id="local-chat-input" className="flex-1 resize-none rounded-xl border border-theme-border bg-theme-bg p-3" rows={2}
          value={input} onChange={event => setInput(event.target.value)} placeholder={ready ? 'Message your local model' : statusText}
          onKeyDown={event => { if (shouldSendMessage(event, sendKey.mode)) { event.preventDefault(); send() } }} />
        {streaming
          ? <button type="button" className="pixel-metal-control px-4" onClick={() => abortRef.current?.abort()} aria-label="Stop"><Square size={16} /></button>
          : <button type="submit" className="pixel-metal-control px-4" disabled={!ready || !input.trim()} aria-label="Send"><Send size={16} /></button>}
      </form>
    </section>
  )
}
