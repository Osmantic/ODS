import { useCallback, useEffect, useRef, useState } from 'react'
import { connectionBundle, readSharing } from './pixelSharingForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'

export default function PixelSharingSettings() {
  const [snapshot, setSnapshot] = useState(null)
  const [label, setLabel] = useState('')
  const [days, setDays] = useState(30)
  const [issued, setIssued] = useState(null)
  const [baseUrl, setBaseUrl] = useState('')
  const baseUrlEdited = useRef(false)
  const [busy, setBusy] = useState(false)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [clock, setClock] = useState(Date.now)
  const mounted = useRef(false)
  const pending = useRef(false)
  const controller = useRef(null)
  const sequence = useRef(0)

  const request = useCallback(async (action = null, payload = null) => {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setError('')
    const seq = ++sequence.current
    const abort = new AbortController()
    controller.current = abort
    const timeout = setTimeout(() => abort.abort(), 10000)
    const current = () => mounted.current && sequence.current === seq
    try {
      const response = await fetch('/api/pixel/inference-sharing' + (action ? '/' + action : ''), {
        method: action ? 'POST' : 'GET', signal: abort.signal,
        ...(payload ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}),
      })
      if (!current()) return
      if (!response.ok) throw new Error('Sharing request failed')
      const result = await response.json()
      const next = readSharing(result)
      const defaultUrl = `http://127.0.0.1:${next.transport.port}/v1`
      if (action && next.configuration.revision !== payload.expectedRevision + 1) throw new Error('Unknown revision')
      if (action === 'issue') connectionBundle(result, defaultUrl)
      if (!current()) return
      setSnapshot(next)
      if (!baseUrlEdited.current) setBaseUrl(defaultUrl)
      setStale(false)
      if (action === 'issue') {
        setIssued(result)
        setLabel('')
        setNotice('Device key created. Copy it now; it cannot be retrieved later.')
      } else if (action === 'start' || action === 'stop') setNotice('Sharing service operation started. Readiness will be checked below.')
      else if (action === 'revoke') {
        setIssued(previous => previous?.credential?.id === payload.deviceId ? null : previous)
        setNotice('Device revoked. Pending inference will be cancelled; completed output cannot be withdrawn.')
      }
      if (!action) setIssued(previous => next.configuration.devices.some(device => device.id === previous?.credential?.id
        && !device.revoked && device.expiresAt * 1000 > Date.now()) ? previous : null)
    } catch {
      if (!current()) return
      if (action) setStale(true)
      setError(action
        ? 'Operation result is unknown. Reload before retrying. An issued key is not recoverable; revoke that device and create another if needed.'
        : 'Sharing settings are unavailable. Reload to try again.')
    } finally {
      clearTimeout(timeout)
      if (current()) { pending.current = false; setBusy(false) }
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    request()
    return () => { mounted.current = false; sequence.current++; pending.current = false; controller.current?.abort() }
  }, [request])

  useEffect(() => {
    if (snapshot?.runtime.status !== 'starting' || stale) return undefined
    const timer = setInterval(() => request(), 2000)
    return () => clearInterval(timer)
  }, [snapshot?.runtime.status, stale, request])

  const config = snapshot?.configuration
  const route = snapshot?.activeRoute
  const now = Math.max(clock, Date.now())
  useEffect(() => {
    const future = config?.devices.filter(device => !device.revoked)
      .map(device => device.expiresAt * 1000).filter(expires => expires > now) || []
    if (!future.length) return undefined
    // Browser timers cannot represent the entire maximum key lifetime.
    const timer = setTimeout(() => setClock(Date.now()), Math.min(2147483647, Math.min(...future) - now + 1))
    return () => clearTimeout(timer)
  }, [config, now])
  useEffect(() => {
    const expiresAt = issued?.configuration.devices.find(device => device.id === issued.credential.id)?.expiresAt
    if (issued && expiresAt * 1000 <= now) {
      setIssued(null)
      setNotice('The newly issued device key has expired. Create a new key to connect.')
    }
  }, [issued, now])
  const locked = busy || stale || !snapshot
  const validLabel = typeof label === 'string' && /^[\x20-\x7e]{1,256}$/.test(label) && label === label.trim()
  const validDays = Number.isInteger(days) && days >= 1 && days <= 365
  const activeDevice = config?.devices.some(device => !device.revoked && device.expiresAt * 1000 > now
    && device.catalogId === route?.catalogId && device.runtimeModelId === route?.runtimeModelId)

  function issue() {
    if (locked || !route || !validLabel || !validDays) return
    setIssued(null)
    setNotice('')
    request('issue', { expectedRevision: config.revision, settings: {
      label, catalogId: route.catalogId, runtimeModelId: route.runtimeModelId, ttlSeconds: days * 86400,
      maxConcurrent: 1, maxOutputTokens: Math.min(4096, route.contextLength), deadlineSeconds: 120, requestsPerMinute: 60,
    } })
  }

  async function copyConnection() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(connectionBundle(issued, baseUrl), null, 2))
      setNotice('Connection settings copied. They contain a secret device key; share only with the intended device.')
    } catch { setError('Could not copy. Check the /v1 URL and clipboard permission.') }
  }

  function confirmAction() {
    if (!confirm || locked) return
    const action = confirm
    setConfirm(null)
    request(action, { expectedRevision: config.revision })
  }

  return <section className="rounded-xl border border-theme-border bg-theme-card p-5 space-y-4" aria-labelledby="pixel-sharing-title">
    <div className="flex items-center justify-between gap-3">
      <div><h2 id="pixel-sharing-title" className="text-lg font-semibold">Share inference with your devices</h2>
        <p className="text-sm text-theme-text-muted">Use this ODS model from an assistant on another device. Tools and permissions stay on that device.</p></div>
      <button className={buttonStyle} disabled={busy} onClick={() => request()}>Reload sharing</button>
    </div>
    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    {notice && <p role="status" className="text-sm text-theme-text-muted">{notice}</p>}
    {!snapshot ? <p className="text-sm text-theme-text-muted">{busy ? 'Loading sharing settings…' : 'Sharing settings could not be loaded.'}</p> : <>
      <div className="grid gap-3 text-sm md:grid-cols-3">
        <div><span className="text-theme-text-muted">Active model</span><p>{route?.runtimeModelId || 'No verified local model'}</p></div>
        <div><span className="text-theme-text-muted">Sharing service</span><p>{snapshot.runtime.status}</p></div>
        <div><span className="text-theme-text-muted">Device admission</span><p>{config.enabled ? 'Enabled' : 'Disabled'}</p></div>
      </div>
      <p className="text-sm text-theme-text-muted">Keys are limited to this active model. To share another model, select it in ODS Models first. A model change pauses incompatible device requests.</p>
      <div className="flex flex-wrap gap-2">
        <button className={buttonStyle} disabled={locked || !route || !activeDevice || snapshot.runtime.status === 'starting'} onClick={() => setConfirm('start')}>Start sharing</button>
        <button className={buttonStyle} disabled={locked || snapshot.runtime.status === 'starting'} onClick={() => setConfirm('stop')}>Stop sharing</button>
      </div>
      {confirm && <div role="dialog" aria-label="Confirm inference sharing" className="rounded border border-amber-500/40 p-4 space-y-3">
        <p className="text-sm">{confirm === 'start'
          ? `Enable issued device keys and build/start only the sharing service on 127.0.0.1:${snapshot.transport.port}? The model router and global ODS provider mode will not be changed.`
          : 'Disable device requests and stop only the sharing service? Active inference will be cancelled. This does not undo completed output.'}</p>
        <button className={buttonStyle} disabled={locked} onClick={confirmAction}>Confirm {confirm === 'start' ? 'start' : 'stop'}</button>{' '}
        <button className={buttonStyle} onClick={() => setConfirm(null)}>Cancel</button>
      </div>}
      <div className="grid gap-3 md:grid-cols-[1fr_10rem_auto] items-end">
        <label className="text-sm">Device label<input className={inputStyle} value={label} maxLength={256} onChange={event => setLabel(event.target.value)} placeholder="My laptop" disabled={locked} /></label>
        <label className="text-sm">Expires in days<input className={inputStyle} type="number" min={1} max={365} value={days} onChange={event => setDays(event.target.value === '' ? '' : Number(event.target.value))} disabled={locked} /></label>
        <button className={buttonStyle} disabled={locked || !route || !validLabel || !validDays || config.devices.length >= 64} onClick={issue}>Create device key</button>
      </div>
      {issued && <div className="rounded-lg border border-theme-border p-4 space-y-3">
        <h3 className="font-medium">One-time connection settings</h3>
        <p className="text-sm text-theme-text-muted">On your laptop, forward this host’s 127.0.0.1:{snapshot.transport.port} through authenticated SSH, or use your explicitly configured HTTPS ingress. The key grants inference only, not SSH or computer access.</p>
        <label className="block text-sm">Laptop connection URL<input className={inputStyle} value={baseUrl} onChange={event => { baseUrlEdited.current = true; setBaseUrl(event.target.value) }} spellCheck={false} /></label>
        <label className="block text-sm">Device API key<input className={inputStyle} type="password" value={issued.credential.key} readOnly autoComplete="off" /></label>
        <p className="text-sm">OpenAI-compatible model: <code>ods/shared</code>. Test the connection on the client before choosing it as the assistant’s leader.</p>
        <button className={buttonStyle} onClick={copyConnection}>Copy connection settings</button>{' '}
        <button className={buttonStyle} onClick={() => setIssued(null)}>Dismiss key</button>
      </div>}
      <ul className="space-y-2" aria-label="Inference devices">
        {config.devices.map(device => <li key={device.id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-theme-border p-3 text-sm">
          <div><p className="font-medium">{device.label}</p><p className="text-theme-text-muted">{device.runtimeModelId} · {device.revoked ? 'Revoked' : device.expiresAt * 1000 <= now ? 'Expired' : `Expires ${new Date(device.expiresAt * 1000).toLocaleDateString()}`}</p></div>
          <button className={buttonStyle} disabled={locked || device.revoked} onClick={() => request('revoke', { expectedRevision: config.revision, deviceId: device.id })}>Revoke {device.label}</button>
        </li>)}
      </ul>
      <p className="text-xs text-theme-text-muted">Each key allows one concurrent request, 60 requests/minute, a 120-second deadline and up to 4,096 output tokens (bounded by model context). Keys are never shown again or stored in browser storage. Native Windows host storage is not supported yet.</p>
    </>}
  </section>
}
