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

  return <div className="p-2 text-xs">
    <label className="flex items-center gap-2"><input type="checkbox" checked={quiet} disabled={busy || !chatId} onChange={event => change(event.target.checked)} />Don’t remember this chat</label>
    <p className="mt-1 text-theme-text-muted">Portal won’t learn from this chat or use what it remembers about you here.</p>
    {error && <p role="alert" className="mt-1 text-red-400">{error}</p>}
  </div>
}
