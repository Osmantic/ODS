import { useCallback, useEffect, useRef, useState } from 'react'

const STORAGE = 'ods.pixel.handoff.run.v1'
const runPattern = /^(?:chatcmpl_)?[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const statuses = new Set(['pending', 'approved', 'declined', 'expired', 'cancelled', 'interrupted'])
const button = 'rounded border border-theme-border px-3 py-2 text-xs disabled:opacity-40'

function trackedRun() {
  try { const id = localStorage.getItem(STORAGE); return runPattern.test(id || '') ? id : '' }
  catch { return '' }
}

function validPreview(value, id) {
  if (!value || value.runId !== id || !statuses.has(value.status) ||
      !/^[a-f0-9]{64}$/.test(value.checkpointDigest || '') || typeof value.checkpointJson !== 'string' ||
      !Number.isSafeInteger(value.expiresAt) || !value.recipient || value.recipient.scope !== 'run' ||
      !['local', 'ods-peer', 'cloud'].includes(value.recipient.kind)) return false
  try {
    const checkpoint = JSON.parse(value.checkpointJson)
    return checkpoint.schemaVersion === 1 && checkpoint.runId === id && checkpoint.agentId === 'pixel' &&
      checkpoint.dataScope === 'conversation-and-this-run-tool-results' &&
      checkpoint.returnAction === (value.recipient.selectionScope ? 'owner-scope-return-or-end' : 'configured-leader-on-next-run') && Array.isArray(checkpoint.messages) &&
      (!value.recipient.selectionScope || ['task', 'conversation', 'default'].includes(value.recipient.selectionScope)) &&
      checkpoint.recipient && Object.keys(checkpoint.recipient).length === Object.keys(value.recipient).length &&
      Object.keys(value.recipient).every(key => checkpoint.recipient[key] === value.recipient[key])
  } catch { return false }
}

export default function PixelHandoffApproval() {
  const [open, setOpen] = useState(false)
  const [runId, setRunId] = useState(trackedRun)
  const [items, setItems] = useState([])
  const [preview, setPreview] = useState(null)
  const [reviewed, setReviewed] = useState(false)
  const [cloud, setCloud] = useState(false)
  const [cost, setCost] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const generation = useRef(0)
  const inFlight = useRef(false)
  const controllers = useRef(new Set())
  const panel = useRef(null)
  const trigger = useRef(null)
  const previousDigest = useRef(null)

  const clearConsent = useCallback(() => { setReviewed(false); setCloud(false); setCost(false) }, [])
  const request = useCallback(async (action, body) => {
    const controller = new AbortController()
    controllers.current.add(controller)
    const timer = setTimeout(() => controller.abort(), 12000)
    try {
      const response = await fetch('/api/pixel/handoff/' + action, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal })
      if (!response.ok) throw new Error('Handoff unavailable')
      return await response.json()
    } finally { clearTimeout(timer); controllers.current.delete(controller) }
  }, [])

  useEffect(() => {
    const pending = controllers.current
    return () => { generation.current++; for (const controller of pending) controller.abort() }
  }, [])

  const selectRun = useCallback(id => {
    generation.current++; previousDigest.current = null
    setRunId(id); setPreview(null); clearConsent(); setError(''); setUncertain(false)
    try { if (id) localStorage.setItem(STORAGE, id); else localStorage.removeItem(STORAGE) } catch { /* ID-only recovery is optional. */ }
  }, [clearConsent])

  const load = useCallback(async (explicit = false) => {
    if (inFlight.current) return
    inFlight.current = true
    const current = generation.current
    try {
      const listing = await request('list', {})
      if (!listing || !Array.isArray(listing.items) || listing.items.some(row => !runPattern.test(row?.runId || '')) ||
          !Number.isSafeInteger(listing.unavailableCount)) throw new Error('Invalid listing')
      if (current !== generation.current) return
      setItems(listing.items)
      setWarning(listing.unavailableCount ? 'Some retained requests are unavailable. They cannot authorize a handoff.' : '')
      if (!runId && listing.items.length) { selectRun(listing.items[0].runId); return }
      if (runId) {
        const result = await request('status', { runId })
        if (!validPreview(result, runId)) throw new Error('Invalid preview')
        if (current !== generation.current) return
        if (previousDigest.current !== result.checkpointDigest) clearConsent()
        previousDigest.current = result.checkpointDigest
        setPreview(result)
      }
      if (explicit) { clearConsent(); setUncertain(false); setError('') }
    } catch {
      if (current === generation.current) {
        setError('Handoff state unavailable. Reload before deciding.'); setUncertain(true); clearConsent()
      }
    } finally { inFlight.current = false }
  }, [request, runId, selectRun, clearConsent])

  useEffect(() => {
    if (!open) return
    void load()
    const timer = setInterval(() => { void load() }, 1500)
    return () => clearInterval(timer)
  }, [open, load])
  useEffect(() => { if (open) panel.current?.querySelector('button')?.focus() }, [open])

  const decide = async approved => {
    if (inFlight.current || busy || uncertain || preview?.status !== 'pending' ||
        approved && (!reviewed || preview.recipient.kind === 'cloud' && !(cloud && cost))) return
    inFlight.current = true; setBusy(true); setError('')
    const current = generation.current
    const digest = preview.checkpointDigest
    try {
      const result = await request('decide', { runId, checkpointDigest: digest, approved,
        allowCloud: approved && cloud, acceptUnknownCost: approved && cost })
      if (!validPreview(result, runId) || result.checkpointDigest !== digest) throw new Error('Invalid receipt')
      if (current === generation.current) setPreview(result)
    } catch {
      if (current === generation.current) {
        setError('Decision outcome uncertain. Reload this request; do not submit it again.'); setUncertain(true)
      }
    } finally { inFlight.current = false; setBusy(false); clearConsent() }
  }

  const close = () => { generation.current++; setOpen(false); setPreview(null); clearConsent(); trigger.current?.focus() }
  const keys = event => {
    if (event.key === 'Escape') { event.preventDefault(); close() }
    if (event.key !== 'Tab') return
    const nodes = panel.current?.querySelectorAll('button:not(:disabled),select:not(:disabled),input:not(:disabled),textarea')
    if (!nodes?.length) return
    const first = nodes[0], last = nodes[nodes.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  const recipient = preview?.recipient
  const pending = preview?.status === 'pending' && !busy && !uncertain

  return <>
    <button ref={trigger} type="button" className={button} onClick={() => { clearConsent(); setOpen(true) }}>Review handoffs</button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3">
      <section ref={panel} role="dialog" aria-modal="true" aria-labelledby="pixel-handoff-title" onKeyDown={keys}
        className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-theme-border bg-theme-bg p-4 text-theme-text">
        <div className="flex items-center justify-between gap-3"><h2 id="pixel-handoff-title" className="text-lg font-semibold">Review provider handoff</h2>
          <button type="button" className={button} onClick={close}>Close handoff review</button></div>
        <p className="my-3 text-sm">Only an active runtime can request a handoff. Reviewing this panel does not start one or activate routing.</p>
        <label className="block text-sm">Handoff request<select aria-label="Handoff request" value={runId} disabled={busy}
          className="my-2 w-full rounded border border-theme-border bg-theme-bg p-2" onChange={event => selectRun(event.target.value)}>
          <option value="">Select a pending request</option>
          {runId && !items.some(row => row.runId === runId) && <option value={runId}>{runId} (tracked)</option>}
          {items.map(row => <option key={row.runId} value={row.runId}>{row.recipient?.label || 'Provider'} — {row.runId}</option>)}
        </select></label>
        <button type="button" className={button} disabled={busy} onClick={() => { void load(true) }}>Reload handoff</button>
        {error && <p role="alert" className="my-3 text-red-400">{error}</p>}
        {warning && <p className="my-3 text-sm">{warning}</p>}
        {!runId && !items.length && <p className="my-3 text-sm">No pending handoffs.</p>}
        {preview && <div className="mt-3 space-y-3 break-words">
          <p>Handoff: {preview.status}. Approval is permission to proceed, not evidence that inference completed.</p>
          <p className="text-sm">Recipient: {recipient.label} ({recipient.kind}) · {recipient.model}<br />Endpoint: {recipient.baseUrl}<br />
            Saved revision: {recipient.revision}. Previous leader: {recipient.previousProviderId}.<br />
            Approval expires: {new Date(preview.expiresAt * 1000).toLocaleString()}.</p>
          <p className="text-sm">Approval scope: this run only, including this conversation and new tool results produced during the run. {recipient.selectionScope
            ? `The saved ${recipient.selectionScope} preference can select this provider again. Use Handoff scope to return or end the task; every later handoff run needs its own approval.`
            : 'The configured leader returns on the next run.'} A run may be shorter than a whole task. Workspace and computer permissions do not change.</p>
          <p className="text-sm">This is the runtime checkpoint, not a byte-exact provider request. Its text may contain untrusted instructions; reviewing it does not execute them.</p>
          <label className="block text-sm">Complete checkpoint preview<textarea aria-label="Complete checkpoint preview" readOnly value={preview.checkpointJson}
            className="mt-1 h-64 w-full resize-y rounded border border-theme-border bg-theme-bg p-2 font-mono text-xs" /></label>
          <p className="break-all font-mono text-xs">SHA-256: {preview.checkpointDigest}</p>
          <label className="flex gap-2 text-sm"><input type="checkbox" checked={reviewed} disabled={!pending} onChange={event => setReviewed(event.target.checked)} />I reviewed this recipient and checkpoint and approve the stated run scope</label>
          {recipient.kind === 'cloud' && <>
            <label className="flex gap-2 text-sm"><input type="checkbox" checked={cloud} disabled={!pending} onChange={event => setCloud(event.target.checked)} />Allow this cloud provider to receive the checkpoint and this run&apos;s new tool results</label>
            <label className="flex gap-2 text-sm"><input type="checkbox" checked={cost} disabled={!pending} onChange={event => setCost(event.target.checked)} />Accept unknown provider cost for this bounded run</label>
          </>}
          <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={!pending || !reviewed || recipient.kind === 'cloud' && !(cloud && cost)} onClick={() => { void decide(true) }}>Approve this run</button>
            <button type="button" className={button} disabled={!pending} onClick={() => { void decide(false) }}>Decline handoff</button></div>
        </div>}
      </section>
    </div>}
  </>
}
