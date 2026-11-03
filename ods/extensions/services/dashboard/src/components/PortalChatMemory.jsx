import { useEffect, useState } from 'react'

// "Don't remember this chat": Portal neither learns from this chat nor uses its memories in it.
// The integrations service enforces it; this browser remembers the choice to show it again.
const KEY = 'portal-unremembered-chats'

function readQuiet() {
  try {
    const value = JSON.parse(window.localStorage.getItem(KEY) || '[]')
    return Array.isArray(value) ? value.filter(item => typeof item === 'string') : []
  } catch {
    return []
  }
}

function writeQuiet(chatId, quiet) {
  const next = readQuiet().filter(item => item !== chatId)
  if (quiet) next.push(chatId)
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next.slice(-1000)))
  } catch {
    // Private windows can refuse storage; the service still keeps the choice.
  }
}

export default function PortalChatMemory({ chatId }) {
  const [quiet, setQuiet] = useState(() => readQuiet().includes(chatId))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { setQuiet(readQuiet().includes(chatId)); setError('') }, [chatId])

  const change = async next => {
    setBusy(true); setError('')
    try {
      const response = await fetch('/api/pixel/integrations/memory-chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chatId, remember: !next }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Memory is unavailable. Try again.')
      writeQuiet(chatId, next)
      setQuiet(next)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy(false)
    }
  }

  // A switch, not a checkbox: checkboxes in the chat belong to Portal's answers (task lists).
  return <div className="p-2 text-xs">
    <button type="button" role="switch" aria-checked={quiet} aria-describedby="portal-chat-memory-note" disabled={busy || !chatId}
      onClick={() => change(!quiet)} className="flex w-full items-center justify-between gap-2 text-left disabled:opacity-60">
      <span>Don’t remember this chat</span>
      <span aria-hidden="true" className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors ${quiet ? 'bg-theme-accent' : 'bg-theme-border'}`}>
        <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform ${quiet ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
      </span>
    </button>
    <p id="portal-chat-memory-note" className="mt-1 text-theme-text-muted">Portal won’t learn from this chat or use what it remembers about you here.</p>
    {error && <p role="alert" className="mt-1 text-red-400">{error}</p>}
  </div>
}
