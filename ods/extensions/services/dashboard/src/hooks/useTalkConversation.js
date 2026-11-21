import { useCallback, useEffect, useRef, useState } from 'react'

export const TALK_HISTORY_KEY = 'ods-talk-conversation-v1'
const RECOVERY_NOTICE = 'The last response could not be confirmed. Start a new conversation to continue; the previous message will not be sent again.'
const UNAVAILABLE_NOTICE = 'This saved conversation is no longer available for this owner session. Start a new conversation.'
const STORAGE_NOTICE = 'This browser could not save the conversation. Keep this page open to retain its history.'
const usableStates = new Set(['new', 'ready'])

// Persist one envelope so history can never be paired with a different ID.
// Files, blob URLs, tool-approval payloads and transport IDs stay in memory.
export function savedMessages(messages) {
  const textOrNull = value => typeof value === 'string' ? value : null
  return messages.map(message => ({
    id: message.id, role: message.role, text: message.text,
    status: message.status, fileName: textOrNull(message.fileName),
    attachmentKind: ['text', 'image'].includes(message.attachmentKind) ? message.attachmentKind : null,
    errorDetail: textOrNull(message.errorDetail), warning: textOrNull(message.warning),
  }))
}

function readSaved() {
  const raw = localStorage.getItem(TALK_HISTORY_KEY)
  if (!raw) return null
  const value = JSON.parse(raw)
  if (value?.version !== 1 || typeof value.id !== 'string' || !value.id || value.id.length > 128
      || !Number.isFinite(value.expiresAt) || !Array.isArray(value.messages)
      || value.messages.some(m => !m || typeof m.id !== 'string' || typeof m.text !== 'string'
        || !['user', 'assistant'].includes(m.role) || !['done', 'pending', 'error'].includes(m.status))) {
    throw new Error('invalid saved conversation')
  }
  return value
}

async function responseError(response, fallback) {
  const body = await response.json().catch(() => ({}))
  return new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || fallback)
}

export default function useTalkConversation(welcomeMessage) {
  const [session, setSession] = useState({ id: null, expiresAt: null, messages: [welcomeMessage] })
  const [hydrated, setHydrated] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [storageNotice, setStorageNotice] = useState('')
  const sessionRef = useRef(session)
  const blockedRef = useRef(false)
  const allocationRef = useRef(null)
  const liveRef = useRef(true)
  const setCurrent = useCallback(value => {
    sessionRef.current = value
    setSession(value)
  }, [])
  const block = useCallback(message => {
    blockedRef.current = true
    setBlocked(true)
    setNotice(message)
  }, [])
  const setMessages = useCallback(update => {
    // The ref is synchronous: a send that just allocated an ID must append to
    // that new envelope, even before React paints it.
    const current = sessionRef.current
    setCurrent({ ...current, messages: typeof update === 'function' ? update(current.messages) : update })
  }, [setCurrent])

  useEffect(() => {
    liveRef.current = true
    let cancelled = false
    const controller = new AbortController()
    async function restore() {
      let saved
      try { saved = readSaved() } catch {
        if (!cancelled) block('Saved conversation history could not be read. Start a new conversation.')
      }
      if (saved) {
        try {
          const response = await fetch(`/api/talk/session/${encodeURIComponent(saved.id)}`, {
            credentials: 'same-origin', signal: controller.signal,
          })
          if (!response.ok) throw new Error(UNAVAILABLE_NOTICE)
          const binding = await response.json()
          if (binding.conversation_id !== saved.id || binding.expires_at !== saved.expiresAt
              || binding.expires_at * 1000 <= Date.now()) throw new Error(UNAVAILABLE_NOTICE)
          if (cancelled) return
          const interrupted = saved.messages.some(m => m.status === 'pending')
          setCurrent({
            id: saved.id, expiresAt: saved.expiresAt,
            messages: savedMessages(saved.messages).map(m => m.status === 'pending'
              ? { ...m, status: 'error', errorDetail: 'This response was interrupted. It has not been sent again.' }
              : m),
          })
          if (!usableStates.has(binding.state)) block(RECOVERY_NOTICE)
          else if (interrupted) setNotice('The previous response was interrupted. Its text is shown below; it has not been sent again.')
        } catch (error) {
          if (!cancelled) block(error.message === UNAVAILABLE_NOTICE ? UNAVAILABLE_NOTICE
            : 'The saved conversation could not be checked. Its history is hidden until this page can reconnect.')
        }
      }
      if (!cancelled) setHydrated(true)
    }
    void restore()
    return () => { cancelled = true; liveRef.current = false; controller.abort() }
  }, [block, setCurrent])

  useEffect(() => {
    if (!hydrated || !session.id) return
    try {
      localStorage.setItem(TALK_HISTORY_KEY, JSON.stringify({
        version: 1, id: session.id, expiresAt: session.expiresAt,
        messages: savedMessages(session.messages),
      }))
      setStorageNotice('')
    } catch { setStorageNotice(STORAGE_NOTICE) }
  }, [hydrated, session])

  const allocate = useCallback(() => {
    if (allocationRef.current) return allocationRef.current
    setBusy(true)
    const pending = (async () => {
      try {
        const response = await fetch('/api/talk/session', { method: 'POST', credentials: 'same-origin' })
        if (!response.ok) throw await responseError(response, 'A new conversation could not be started.')
        const binding = await response.json()
        if (typeof binding.conversation_id !== 'string' || !binding.conversation_id
            || !Number.isFinite(binding.expires_at) || binding.expires_at * 1000 <= Date.now()) {
          throw new Error('The server did not return a valid conversation.')
        }
        if (!liveRef.current) throw new Error('Talk closed before the conversation was ready.')
        setCurrent({ id: binding.conversation_id, expiresAt: binding.expires_at, messages: [welcomeMessage] })
        blockedRef.current = false
        setBlocked(false)
        setNotice('')
        return binding.conversation_id
      } catch (error) {
        if (liveRef.current) setNotice(error.message)
        throw error
      } finally {
        allocationRef.current = null
        if (liveRef.current) setBusy(false)
      }
    })()
    allocationRef.current = pending
    return pending
  }, [setCurrent, welcomeMessage])

  const ensureConversation = useCallback(async () => {
    if (!hydrated || blockedRef.current) throw new Error('Start a new conversation before sending.')
    const current = sessionRef.current
    if (!current.id) return allocate()
    if (current.expiresAt * 1000 <= Date.now()) {
      block(UNAVAILABLE_NOTICE)
      throw new Error(UNAVAILABLE_NOTICE)
    }
    return current.id
  }, [allocate, block, hydrated])

  // Never turn an ambiguous transport failure into an automatic resend.
  const markUnknown = useCallback(() => block(RECOVERY_NOTICE), [block])
  const hideHistory = useCallback(() => {
    setCurrent({ id: null, expiresAt: null, messages: [welcomeMessage] })
    block(UNAVAILABLE_NOTICE)
  }, [block, setCurrent, welcomeMessage])

  return {
    messages: session.messages, setMessages, conversationId: session.id,
    ready: hydrated && !blocked && !busy, hydrated, busy, notice, storageNotice,
    ensureConversation, newConversation: allocate, markUnknown, hideHistory,
  }
}
