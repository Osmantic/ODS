import { useCallback, useEffect, useRef, useState } from 'react'

const key = 'ods.pixel.advice.setup.v1'
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
const hex = /^[a-f0-9]{64}$/
const done = new Set(['completed', 'cancelled', 'failed', 'interrupted'])
const button = 'rounded border border-theme-border px-3 py-2 text-xs disabled:opacity-40'
function stored() { try { const id = localStorage.getItem(key); return uuid.test(id || '') ? id : null } catch { return null } }
function validJob(job) { return job && uuid.test(job.jobId || '') && ['running', 'cancelling', ...done].includes(job.status) }
function validReadiness(value) {
  return value && ['ready', 'missing', 'drift', 'unsupported', 'not-configured'].includes(value.status)
    && Number.isSafeInteger(value.revision) && value.revision >= 0 && typeof value.host === 'string'
    && (value.sourceSha256 === null || hex.test(value.sourceSha256)) && Array.isArray(value.candidates)
    && value.candidates.every(c => hex.test(c.id) && typeof c.path === 'string' && typeof c.version === 'string' && typeof c.canPrepare === 'boolean')
    && (value.job === null || validJob(value.job))
    && (value.status !== 'ready' || (hex.test(value.sourceSha256) && /^runtime-[a-f0-9]{32}$/.test(value.runtimeId)))
}

export default function PixelAdviceRuntime({ onReadyChange, title = 'Advisory runtime', disabled = false }) {
  const [readiness, setReadiness] = useState(null)
  const [selected, setSelected] = useState('')
  const [consent, setConsent] = useState(false)
  const [jobId, setJobId] = useState(stored)
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const tracked = useRef(jobId)
  const dismissed = useRef(null)
  const fingerprint = useRef('')
  const inflight = useRef(false)
  const polling = useRef(false)
  const jobVersion = useRef(0)
  const controllers = useRef(new Set())
  const epoch = useRef(0)
  const notify = useRef(onReadyChange)
  notify.current = onReadyChange

  const request = useCallback(async (path, body) => {
    const controller = new AbortController(); controllers.current.add(controller)
    const timer = setTimeout(() => controller.abort(), 12000)
    try {
      const response = await fetch('/api/pixel/advice-runtime' + path, { ...(body ? {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      } : {}), signal: controller.signal })
      if (!response.ok) throw new Error('Setup request failed')
      return await response.json()
    } finally { clearTimeout(timer); controllers.current.delete(controller) }
  }, [])

  const refresh = useCallback(async () => {
    const current = ++epoch.current
    notify.current?.(false)
    try {
      const value = await request('')
      if (!validReadiness(value)) throw new Error('Invalid readiness')
      if (current !== epoch.current) return
      const next = JSON.stringify([value.host, value.revision, value.sourceSha256, value.candidates])
      if (next !== fingerprint.current) {
        fingerprint.current = next; setConsent(false)
        setSelected(old => value.candidates.some(c => c.id === old && c.canPrepare) ? old : value.candidates.find(c => c.canPrepare)?.id || '')
      }
      setReadiness(value); notify.current?.(value.status === 'ready'); setError('')
      if (!tracked.current && value.job && value.job.jobId !== dismissed.current) {
        tracked.current = value.job.jobId; setJobId(value.job.jobId); setJob(value.job)
      }
    } catch { if (current === epoch.current) { setReadiness(null); setConsent(false); setError('Runtime readiness is unknown. Refresh before preparing or using this runtime.') } }
  }, [request])

  useEffect(() => {
    const pending = controllers.current
    void refresh()
    return () => { epoch.current++; for (const controller of pending) controller.abort() }
  }, [refresh])

  const inspect = useCallback(async () => {
    if (!jobId || polling.current || inflight.current) return
    polling.current = true
    const version = jobVersion.current
    try {
      const result = await request('/status', { jobId })
      if (!validJob(result) || result.jobId !== jobId) throw new Error('Invalid setup job')
      if (tracked.current !== jobId || version !== jobVersion.current) return
      setJob(result); setError('')
      if (done.has(result.status)) await refresh()
    } catch { if (tracked.current === jobId && version === jobVersion.current) setError('Setup status is unknown. Check the tracked setup; closing this panel does not stop it.') }
    finally { polling.current = false }
  }, [jobId, request, refresh])

  useEffect(() => {
    if (!jobId || done.has(job?.status)) return
    void inspect()
    const timer = setInterval(() => void inspect(), 1500)
    return () => clearInterval(timer)
  }, [jobId, job?.status, inspect])

  const available = readiness && ['missing', 'drift'].includes(readiness.status)
    && readiness.candidates.some(c => c.id === selected && c.canPrepare) && hex.test(readiness.sourceSha256)
  const busy = submitting || (jobId && !done.has(job?.status))

  async function prepare() {
    if (!available || !consent || busy || disabled || inflight.current) return
    inflight.current = true; setSubmitting(true); setError('')
    jobVersion.current++
    let id
    try {
      id = globalThis.crypto.randomUUID(); localStorage.setItem(key, id)
      tracked.current = id; setJobId(id); setJob(null)
      const result = await request('/prepare', { requestId: id, expectedRevision: readiness.revision,
        sourceSha256: readiness.sourceSha256, candidateId: selected, confirmed: true })
      if (!validJob(result) || result.jobId !== id) throw new Error('Invalid setup job')
      if (tracked.current !== id) return
      setJob(result); setError('')
      if (done.has(result.status)) await refresh()
    } catch { setError(tracked.current === id ? 'Setup start is unconfirmed. Check this setup; it will not be submitted again automatically.' : 'Could not track setup. Nothing was submitted.') }
    finally { setConsent(false); setSubmitting(false); inflight.current = false }
  }

  async function stop() {
    if (!jobId || inflight.current) return
    inflight.current = true; setSubmitting(true); jobVersion.current++
    try {
      const result = await request('/cancel', { jobId })
      if (!validJob(result) || result.jobId !== jobId) throw new Error('Invalid setup job')
      if (tracked.current !== jobId) return
      setJob(result); setError('')
      if (done.has(result.status)) await refresh()
    } catch { if (tracked.current === jobId) setError('Stop setup is unconfirmed. Check the tracked setup again.') }
    finally { inflight.current = false; setSubmitting(false) }
  }

  function forget() {
    if (inflight.current) return
    try { localStorage.removeItem(key) } catch { setError('Could not clear setup tracking.'); return }
    dismissed.current = tracked.current; tracked.current = null; setJobId(null); setJob(null); setConsent(false); setError('')
  }

  return <section aria-label={`${title} setup`} className="space-y-2 rounded border border-theme-border p-3 text-sm">
    <h3 className="font-semibold">{title}</h3>
    <p role="status">Runtime: {readiness?.status || 'unknown'}{readiness ? ` on ${readiness.host}` : ''}</p>
    <p className="text-xs">This is the ODS service host, not necessarily the device displaying this browser. Setup never changes your leader or execution permissions.</p>
    {error && <p role="alert" className="text-amber-400">{error}</p>}
    <button type="button" className={button} onClick={refresh}>Refresh runtime readiness</button>
    {readiness?.status === 'not-configured' && <p>Save Pixel provider settings first. No runtime storage has been created.</p>}
    {readiness?.status === 'unsupported' && <p>Guided private-runtime setup is not available on this platform. No installation was attempted.</p>}
    {readiness && ['missing', 'drift'].includes(readiness.status) && <>
      <label className="block">Python on {readiness.host}<select className="mt-1 w-full rounded border border-theme-border bg-theme-bg p-2" value={selected} onChange={event => { setSelected(event.target.value); setConsent(false) }} disabled={!!busy}>
        <option value="">Select a verified interpreter</option>
        {readiness.candidates.map(c => <option key={c.id} value={c.id} disabled={!c.canPrepare}>{c.path} · Python {c.version}{c.canPrepare ? '' : ' · venv/ensurepip missing'}</option>)}
      </select></label>
      {!readiness.candidates.some(c => c.canPrepare) && <p>An operator must install Python 3.11+ with venv/ensurepip on this host, then refresh. ODS will not install OS packages or request administrator privileges.</p>}
      <label className="flex gap-2"><input type="checkbox" checked={consent} disabled={!!busy} onChange={event => setConsent(event.target.checked)} />Allow a private runtime and dependency download from PyPI on this host. No model call, global Python upgrade, or service restart.</label>
      <button type="button" className={button} disabled={!available || !consent || !!busy || disabled} onClick={prepare}>{readiness.status === 'drift' ? 'Repair private runtime' : 'Prepare private runtime'}</button>
    </>}
    {jobId && <div className="space-y-2">
      <p className="break-all text-xs">Tracked setup: {jobId}</p>
      <p role="status">Setup: {job?.status || 'unknown'}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} onClick={inspect}>Check setup</button>
        {!done.has(job?.status) && <button type="button" className={button} disabled={submitting} onClick={stop}>Stop setup</button>}
        {(done.has(job?.status) || error) && <button type="button" className={button} disabled={submitting} onClick={forget}>Forget tracking (does not stop setup)</button>}
      </div>
      {job?.status === 'failed' && <p>Setup failed or runtime custody changed. Refresh readiness and check host Python/venv availability. Old runtime files and job evidence are retained.</p>}
      <p className="text-xs">An interrupted setup is never replayed automatically. If publication already finished, Stop reports completion; it does not undo that installation.</p>
    </div>}
  </section>
}
