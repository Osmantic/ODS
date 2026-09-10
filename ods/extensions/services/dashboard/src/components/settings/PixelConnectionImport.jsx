import { useEffect, useRef, useState } from 'react'
import { parseBundle, validateProbeResponse, suggestProviderId } from './pixelConnectionBundle'
import { createProvider } from './pixelProviderForm'

const input = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text'
const button = 'rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40'

export default function PixelConnectionImport({ providers, disabled, onBusyChange, onImport }) {
  const [raw, setRaw] = useState('')
  const [review, setReview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [metadata, setMetadata] = useState(null)
  const [id, setId] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const key = useRef(null)
  const pending = useRef(null)
  const mounted = useRef(false)
  const sequence = useRef(0)
  const callbacks = useRef({ onBusyChange, onImport })
  callbacks.current = { onBusyChange, onImport }

  function release(request) {
    if (pending.current !== request) return
    clearTimeout(request.timer)
    pending.current = null
    callbacks.current.onBusyChange(false)
    if (mounted.current) setBusy(false)
  }
  function clear() {
    sequence.current++
    if (pending.current) { pending.current.abort.abort(); release(pending.current) }
    key.current = null
    setRaw(''); setReview(null); setConfirmed(false); setMetadata(null); setError('')
  }
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      sequence.current++
      key.current = null
      if (pending.current) { pending.current.abort.abort(); release(pending.current) }
    }
  }, [])

  function inspectPaste() {
    if (disabled || pending.current) return
    key.current = null
    setReview(null); setConfirmed(false); setMetadata(null); setError('')
    try {
      const value = parseBundle(raw)
      setReview(value); setLabel(value.label)
      setId(suggestProviderId(value.label, new Set(providers.map(p => p.id))))
    } catch { setError('Paste a valid, unexpired ODS connection bundle with an HTTPS or loopback endpoint.') }
  }

  async function probe() {
    if (disabled || pending.current || !confirmed || !review || !raw) return
    let apiKey
    try { parseBundle(raw); apiKey = JSON.parse(raw).credential.apiKey } catch { clear(); setError('Re-paste and review a valid, unexpired connection.'); return }
    if (callbacks.current.onBusyChange(true) === false) return
    const request = { abort: new AbortController(), seq: ++sequence.current, timer: null }
    pending.current = request
    request.timer = setTimeout(() => request.abort.abort(), 25000)
    const current = () => mounted.current && sequence.current === request.seq && !request.abort.signal.aborted
    const payload = JSON.stringify({ bundle: raw, confirmedEndpoint: review.endpoint })
    setRaw(''); key.current = null; setBusy(true); setMetadata(null); setError('')
    try {
      const response = await fetch('/api/pixel/providers/connection-probe', {
        method: 'POST', cache: 'no-store', signal: request.abort.signal,
        headers: { 'Content-Type': 'application/json' }, body: payload,
      })
      if (!current()) return
      if (!response.ok) throw new Error('probe-failed')
      const value = await response.json()
      if (!current()) return
      const result = validateProbeResponse(value, review)
      key.current = apiKey
      setMetadata(result)
    } catch {
      if (mounted.current && sequence.current === request.seq) {
        key.current = null; setMetadata(null)
        setError('Metadata could not be verified. No provider was saved or applied. Clear, re-paste and review before another attempt.')
      }
    } finally {
      apiKey = null
      if (mounted.current && sequence.current === request.seq) setConfirmed(false)
      release(request)
    }
  }

  function add() {
    if (disabled || pending.current || !review || !metadata || !key.current) return
    if (review.expiresAt <= Date.now() / 1000) { clear(); setError('The connection expired. Obtain a new bundle.'); return }
    try {
      const provider = { ...createProvider(id, label, providers), kind: 'ods-peer',
        baseUrl: review.endpoint, model: 'ods/shared', contextTokens: metadata.contextLength,
        maxOutputTokens: metadata.maxOutputTokens, supportsTools: metadata.capabilities.tools,
        supportsVision: metadata.capabilities.vision, reasoning: false, hasCredential: false, enabled: false }
      if (callbacks.current.onImport(provider, key.current) === false) { setError('Provider settings changed or are busy. Review them before adding.'); return }
      clear()
    } catch { setError('Choose a unique valid provider ID and label; at most 32 providers are allowed.') }
  }

  return <section aria-label="Import ODS connection" className="rounded border border-theme-border p-4 space-y-3">
    <h3 className="font-medium">Import an ODS connection</h3>
    <p className="text-sm text-theme-text-muted">Paste a scoped connection copied from another ODS host. Review before sending its key. Nothing is saved or activated by this check.</p>
    {error && <p role="alert">{error}</p>}
    <label className="block text-sm">Connection bundle (private)
      <input className={input} type="password" autoComplete="new-password" spellCheck={false} maxLength={32768}
        disabled={disabled || busy} value={raw} onChange={e => { clear(); setRaw(e.target.value) }} />
    </label>
    <div className="flex gap-2">
      <button className={button} disabled={disabled || busy || !raw} onClick={inspectPaste}>Review endpoint</button>
      <button className={button} onClick={clear}>{busy ? 'Cancel check' : 'Clear connection'}</button>
    </div>
    {review && <div className="space-y-2 text-sm break-all">
      <p>Endpoint: <code>{review.endpoint}</code></p>
      <p>Expected model: {review.expected.runtimeModelId} · Device: {review.deviceId}</p>
      <p>Expires: {new Date(review.expiresAt * 1000).toLocaleString()}</p>
      <p>Loopback means the ODS host, not this browser. Any trusted SSH tunnel must already exist; this page does not create one.</p>
      {raw && <label className="flex gap-2"><input type="checkbox" checked={confirmed} disabled={disabled || busy}
        onChange={e => setConfirmed(e.target.checked)} />Send this connection key only to {review.endpoint} to check model metadata.</label>}
      <button className={button} disabled={disabled || busy || !confirmed || !raw} onClick={probe}>{busy ? 'Checking metadata…' : 'Check connection metadata'}</button>
    </div>}
    {metadata && <div className="space-y-3 text-sm">
      <p role="status">Model metadata matches the connection. Inference and tool execution have not been tested.</p>
      <p>Context: {metadata.contextLength} · Maximum output: {metadata.maxOutputTokens} · Declared tool calling: {metadata.capabilities.tools ? 'yes' : 'no'}</p>
      {!metadata.capabilities.tools && <p className="text-amber-400">This host does not advertise tool support. Importing will not enable it.</p>}
      <label className="block">Imported provider ID<input className={input} value={id} maxLength={64} disabled={disabled} onChange={e => setId(e.target.value)} /></label>
      <label className="block">Imported provider label<input className={input} value={label} maxLength={256} disabled={disabled} onChange={e => setLabel(e.target.value)} /></label>
      <button className={button} disabled={disabled} onClick={add}>Add to provider draft</button>
      <p>Add creates a disabled draft with declared capabilities and reasoning off. Choose its role and capabilities, then Save and separately Apply when ready. Existing providers and roles stay unchanged.</p>
    </div>}
  </section>
}
