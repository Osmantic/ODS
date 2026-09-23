import { useEffect, useState } from 'react'
import ModelTermsContent from './ModelTermsContent'

export default function ModelTermsDetails({ modelId }) {
  const [open, setOpen] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    let current = true
    setLoading(true)
    setError(null)
    setResult(null)
    const deadline = setTimeout(() => {
      if (!current) return
      current = false
      controller.abort()
      setLoading(false)
      setError('Sources and terms took too long to load. Retry.')
    }, 15000)
    ;(async () => {
      try {
        const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/terms`, { signal: controller.signal })
        const body = await response.json()
        if (!current) return
        if (!response.ok) throw new Error('Sources and terms could not be loaded.')
        if (body.modelId !== modelId || typeof body.recordValid !== 'boolean') throw new Error('The terms response does not match this model.')
        setResult(body)
      } catch (reason) {
        if (current && reason?.name !== 'AbortError') setError(reason.message || 'Sources and terms could not be loaded.')
      } finally {
        clearTimeout(deadline)
        if (current) setLoading(false)
      }
    })()
    return () => { current = false; clearTimeout(deadline); controller.abort() }
  }, [open, modelId, attempt])

  return <div className="mt-2 text-xs text-theme-text-muted">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)} className="text-theme-accent underline">Sources and terms</button>
    {open && <section aria-label="Model sources and terms" className="mt-2 space-y-2 rounded-md border border-theme-border p-3">
      {loading && <p role="status">Loading sources and terms…</p>}
      {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)} className="underline">Retry terms</button></div>}
      {result && !result.recordValid && <div role="status"><p>Source and license information is incomplete.</p>{result.errors?.map(message => <p key={message}>{message}</p>)}</div>}
      <ModelTermsContent result={result} />
    </section>}
  </div>
}
