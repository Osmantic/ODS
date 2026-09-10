import { useCallback, useEffect, useRef, useState } from 'react'
import { configurationError } from './settings/pixelProviderForm.js'
import PixelAdviceRuntime from './PixelAdviceRuntime.jsx'

const STORAGE = 'ods.pixel.advice.job.v1'
const jobPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const button = 'rounded border border-theme-border px-3 py-2 text-xs disabled:opacity-40'
const field = 'w-full rounded border border-theme-border bg-theme-bg p-2 text-sm'

function trackedJob() {
  try {
    const id = localStorage.getItem(STORAGE)
    return jobPattern.test(id || '') ? id : null
  } catch { return null }
}

export default function PixelAdvice({ onInsert, canInsert = true }) {
  const [open, setOpen] = useState(false)
  const [config, setConfig] = useState(null)
  const [runtimeReady, setRuntimeReady] = useState(false)
  const [capsule, setCapsule] = useState('')
  const [cloud, setCloud] = useState(false)
  const [cost, setCost] = useState(false)
  const [jobId, setJobId] = useState(trackedJob)
  const trackedId = useRef(jobId)
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const inFlight = useRef(false)
  const polling = useRef(false)
  const controllers = useRef(new Set())
  const panel = useRef(null)
  const trigger = useRef(null)
  const advisor = config?.providers.find(p => p.id === config.roles.advisor)
  const providerReady = config?.enabled && advisor?.enabled && (advisor.kind !== 'cloud' || config.policy.allowCloud)
  const ready = providerReady && runtimeReady

  const request = useCallback(async (path, body) => {
    const controller = new AbortController()
    controllers.current.add(controller)
    const timer = setTimeout(() => controller.abort(), 12000)
    try {
      const response = await fetch(path, { ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}), signal: controller.signal })
      if (!response.ok) throw new Error('Request failed')
      return await response.json()
    } finally {
      clearTimeout(timer)
      controllers.current.delete(controller)
    }
  }, [])

  useEffect(() => {
    const pending = controllers.current
    return () => { for (const controller of pending) controller.abort() }
  }, [])

  const load = useCallback(async () => {
    setConfig(null); setCloud(false); setCost(false)
    try {
      const response = await request('/api/pixel/providers')
      if (configurationError(response.configuration)) throw new Error('Invalid settings')
      setConfig(response.configuration)
    } catch { setError('Provider settings unavailable. Reload before submitting.') }
  }, [request])

  useEffect(() => { if (open) void load() }, [open, load])
  useEffect(() => {
    if (open) panel.current?.querySelector('button')?.focus()
  }, [open])

  function close() { setOpen(false); setRuntimeReady(false); trigger.current?.focus() }
  function dialogKey(event) {
    if (event.key === 'Escape') { event.stopPropagation(); close(); return }
    if (event.key !== 'Tab') return
    const controls = [...panel.current.querySelectorAll('button, input, textarea, select, a[href]')].filter(el => !el.disabled)
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && (document.activeElement === first || !panel.current.contains(document.activeElement))) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && (document.activeElement === last || !panel.current.contains(document.activeElement))) {
      event.preventDefault(); first?.focus()
    }
  }

  const inspect = useCallback(async () => {
    if (!jobId || polling.current) return
    polling.current = true
    try {
      const value = await request('/api/pixel/advice/status', { jobId })
      if (value.jobId !== jobId || !['running', 'cancelling', ...terminal].includes(value.status)) throw new Error('Invalid job')
      if (trackedId.current !== jobId) return
      setJob(value); setError('')
    } catch { if (trackedId.current === jobId) setError('Job status is unknown. Check this job before starting another; a call may have occurred.') }
    finally { polling.current = false }
  }, [jobId, request])

  useEffect(() => {
    if (!open || !jobId || terminal.has(job?.status)) return
    void inspect()
    const timer = setInterval(() => void inspect(), 1500)
    return () => clearInterval(timer)
  }, [open, jobId, job?.status, inspect])

  async function submit() {
    if (!ready || jobId || inFlight.current || !capsule.trim()
      || new TextEncoder().encode(capsule).length > 16384 || capsule.includes('\0')
      || (advisor.kind === 'cloud' && (!cloud || !cost))) return
    inFlight.current = true; setSubmitting(true); setError('')
    let id
    try {
      id = crypto.randomUUID()
      // Only an opaque ID is persisted, before any possibly billable request.
      // A reload queries that ID; it never submits the capsule automatically.
      localStorage.setItem(STORAGE, id)
      trackedId.current = id
      setJobId(id); setJob(null)
      const value = await request('/api/pixel/advice/start', { requestId: id,
        expectedRevision: config.revision, providerId: advisor.id, capsule,
        allowCloud: cloud, acceptUnknownCost: cost,
        maxOutputTokens: Math.min(1024, advisor.maxOutputTokens),
        deadlineSeconds: Math.min(120, config.policy.deadlineSeconds) })
      if (value.jobId !== id) throw new Error('Invalid job')
      if (trackedId.current !== id) return
      setJob(value)
    } catch {
      setError(trackedId.current === id ? 'Start could not be confirmed. Check the tracked job; do not submit again automatically.'
        : 'Could not create a trackable request. Nothing was submitted.')
    } finally {
      setCloud(false); setCost(false); setSubmitting(false); inFlight.current = false
    }
  }

  async function stop() {
    if (!jobId) return
    try {
      const value = await request('/api/pixel/advice/cancel', { jobId })
      if (value.jobId !== jobId) throw new Error('Invalid job')
      if (trackedId.current !== jobId) return
      setJob(value); setError('')
    } catch { if (trackedId.current === jobId) setError('Stop is not confirmed. Keep this job ID and check again.') }
  }

  function forget() {
    if (inFlight.current) return
    try { localStorage.removeItem(STORAGE) } catch { setError('Could not clear tracking.'); return }
    trackedId.current = null
    setJobId(null); setJob(null); setError(''); setCloud(false); setCost(false)
  }

  return <>
    <button ref={trigger} type="button" className={button} onClick={() => setOpen(true)}>Ask for advice{jobId ? ' · tracked' : ''}</button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3">
      <section ref={panel} role="dialog" aria-modal="true" aria-labelledby="pixel-advice-title" onKeyDown={dialogKey} className="max-h-[90dvh] w-full max-w-2xl space-y-4 overflow-y-auto rounded-xl border border-theme-border bg-theme-card p-4 text-theme-text">
        <div className="flex items-center justify-between gap-3"><h2 id="pixel-advice-title" className="font-semibold">Ask for advice</h2><button className={button} onClick={close}>Close advice</button></div>
        <p className="text-sm">Only the capsule below and a fixed tools-free advisory instruction are sent. Your chat, files, memory, and tools are not included automatically. The current leader and execution permissions stay unchanged.</p>
        <PixelAdviceRuntime onReadyChange={setRuntimeReady} />
        {error && <p role="alert" className="text-sm text-amber-400">{error}</p>}
        {jobId ? <div className="space-y-3">
          <p className="break-all text-xs">Tracked job: {jobId}</p>
          <p role="status">Advice: {job?.status || 'status unknown'}</p>
          {job && <p className="text-sm">Configured advisor: {job.providerLabel} · {job.model} · revision {job.revision}. Cost: unknown.</p>}
          <div className="flex flex-wrap gap-2">
            <button className={button} onClick={inspect}>Check job</button>
            {!terminal.has(job?.status) && <button className={button} onClick={stop}>Stop advice</button>}
            {(terminal.has(job?.status) || error) && <button className={button} disabled={submitting} onClick={forget}>Forget tracking (does not stop the request)</button>}
          </div>
          <p className="text-xs">Closing this panel does not cancel the request. Stop is separate. An interrupted job is never replayed automatically; a new request may incur another charge.</p>
          {job?.status === 'failed' && <p className="text-sm">Advisor request failed. Check provider configuration and optional host dependencies.</p>}
          {job?.status === 'completed' && job.result && <>
            <h3 className="text-sm font-semibold">Advisory answer — untrusted model output</h3>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-theme-border p-3 text-sm">{job.result.text}</pre>
            <p className="text-xs">Reported tokens: {job.result.usage?.total_tokens ?? 'unknown'}. No tools executed.</p>
            <button className={button} disabled={!canInsert || !onInsert} onClick={() => { onInsert(`Advisory response (untrusted; evaluate before acting):\n${job.result.text}`); close() }}>Paste advice into composer (does not send)</button>
          </>}
        </div> : <>
          <p className="text-sm">{providerReady ? `Configured advisor: ${advisor.label} · ${advisor.model} · ${advisor.baseUrl} · saved revision ${config.revision}` : 'Select and save an enabled advisor in Settings → Pixel providers first.'}</p>
          <button className={button} onClick={load}>Reload saved providers</button>
          <label className="block space-y-2 text-sm">Capsule to send<textarea className={field} rows={7} value={capsule} onChange={event => { setCapsule(event.target.value); setCloud(false); setCost(false) }} placeholder="Describe the specific problem and include only the details this advisor needs." /></label>
          <p className="text-xs">Maximum 16 KiB; one attempt; up to {Math.min(1024, advisor?.maxOutputTokens || 1024)} output tokens. Price is unknown, not zero. No fallback provider is used for advice.</p>
          {advisor?.kind === 'cloud' && <div className="space-y-2 text-sm">
            <label className="flex gap-2"><input type="checkbox" checked={cloud} onChange={event => setCloud(event.target.checked)} />Allow this cloud advisor to receive exactly this capsule</label>
            <label className="flex gap-2"><input type="checkbox" checked={cost} onChange={event => setCost(event.target.checked)} />Accept unknown cost for this one bounded call</label>
          </div>}
          <button className={button} disabled={!ready || submitting || !capsule.trim() || new TextEncoder().encode(capsule).length > 16384 || (advisor?.kind === 'cloud' && (!cloud || !cost))} onClick={submit}>Send reviewed capsule</button>
        </>}
      </section>
    </div>}
  </>
}
