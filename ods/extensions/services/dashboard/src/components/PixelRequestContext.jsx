import {useMemo, useState} from 'react'
import {boundedHistory} from '../lib/pixelRequestContext'

export default function PixelRequestContext({messages, contextStart, draft, busy}) {
  const [open, setOpen] = useState(false)
  const summary = useMemo(() => {
    if (!open || busy) return null
    const prompt = draft.trim()
    const history = boundedHistory(messages.slice(contextStart), prompt)
    const original = messages.slice(messages.length - history.length)
    const shortened = history.filter((message, index) => message.content !== original[index]?.content).length
    const encoder = new TextEncoder()
    const bytes = [...history.map(message => message.content), prompt].reduce((sum, text) => sum + encoder.encode(text).byteLength, 0)
    return {history:history.length, omitted:messages.length - history.length, shortened, bytes}
  }, [open, busy, draft, messages, contextStart])
  return <div className="p-2 text-xs">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>Request context</button>
    {open && <section aria-label="Next request context" className="mt-2 space-y-2">
      {busy ? <p>Available after the active request finishes.</p> : <>
        <p>{summary.history} earlier messages included; {summary.omitted} omitted; {summary.shortened} shortened.</p>
        <p>{summary.bytes.toLocaleString()} UTF-8 text bytes including the trimmed draft.</p>
        {!draft.trim() && <p>Enter a draft to preview a sendable request.</p>}
        {draft.trim().length > 16384 && <p>This draft exceeds the message limit and cannot be sent.</p>}
        <p>Earlier visible history stays saved. This describes the dashboard request, not model tokens or additional agent instructions.</p>
      </>}
    </section>}
  </div>
}
