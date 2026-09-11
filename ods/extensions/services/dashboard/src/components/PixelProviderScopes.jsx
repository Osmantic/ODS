import { useEffect, useRef, useState } from 'react'

const button = 'rounded border border-theme-border px-3 py-2 text-xs disabled:opacity-40'
const scopes = ['task', 'conversation', 'default']
const labels = { task: 'This task (until End task)', conversation: 'This conversation', default: 'Default for newly begun tasks' }

function valid(value, chatId) {
  return value?.schemaVersion === 1 && value.chatId === chatId && Number.isSafeInteger(value.revision) &&
    value.revision >= 0 && value.runtimeStatus === 'preference-only' && value.checkpointApproval === 'required-each-handoff-run' &&
    (value.taskId === null || /^[a-f0-9-]{36}$/.test(value.taskId || '')) &&
    (value.effectiveScope === null || scopes.includes(value.effectiveScope))
}

export default function PixelProviderScopes({ chatId, sending = false }) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState(null)
  const [configuration, setConfiguration] = useState(null)
  const [scope, setScope] = useState('task')
  const [reviewed, setReviewed] = useState(false)
  const [cloud, setCloud] = useState(false)
  const [cost, setCost] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(false)
  const generation = useRef(0)
  const controllers = useRef(new Set())
  const trigger = useRef(null)
  const panel = useRef(null)
  const resetConsent = () => { setReviewed(false); setCloud(false); setCost(false) }

  useEffect(() => {
    const pending = controllers.current
    generation.current++; setState(null); setConfiguration(null); setOpen(false)
    setReviewed(false); setCloud(false); setCost(false)
    return () => { generation.current++; for (const controller of pending) controller.abort() }
  }, [chatId])

  const request = async (url, body) => {
    const controller = new AbortController(); controllers.current.add(controller)
    const timer = setTimeout(() => controller.abort(), 12000)
    try {
      const response = await fetch(url, { ...(body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), signal: controller.signal })
      if (!response.ok) throw new Error('Request unavailable')
      return await response.json()
    } finally { clearTimeout(timer); controllers.current.delete(controller) }
  }

  const load = async () => {
    if (active.current) return
    active.current = true; setBusy(true); resetConsent()
    const current = generation.current
    try {
      const result = await request('/api/pixel/provider-scopes/status', { chatId })
      const providers = await request('/api/pixel/providers')
      if (!valid(result, chatId) || !Array.isArray(providers?.configuration?.providers) ||
          !Number.isSafeInteger(providers.configuration.revision)) throw new Error('Invalid status')
      if (current !== generation.current) return
      setState(result); setConfiguration(providers.configuration); setUncertain(false); setError('')
    } catch {
      if (current === generation.current) { setUncertain(true); setError('Provider preferences unavailable. Reload before making changes.') }
    } finally { active.current = false; setBusy(false) }
  }

  const target = configuration?.providers.find(provider => provider.id === configuration.roles?.handoff)
  const canChange = !!state && !busy && !uncertain && !sending
  const canSelect = canChange && configuration?.enabled && target?.enabled && reviewed &&
    (scope !== 'task' || !!state.taskId) && (target.kind !== 'cloud' || cloud && cost)
  const mutate = async action => {
    if (active.current || !canChange || action === 'select' && !canSelect) return
    active.current = true; setBusy(true); setError('')
    const current = generation.current
    try {
      const body = { chatId, expectedRevision: state.revision, taskId: state.taskId }
      if (action === 'begin') body.taskId = crypto.randomUUID()
      if (action === 'select' || action === 'return') body.scope = scope
      if (action === 'select') Object.assign(body, { providerId: target.id, providerRevision: configuration.revision,
        allowCloud: target.kind === 'cloud' && cloud, acceptUnknownCost: target.kind === 'cloud' && cost })
      const result = await request('/api/pixel/provider-scopes/' + action, body)
      if (!valid(result, chatId) || result.revision !== state.revision + 1) throw new Error('Invalid receipt')
      if (current === generation.current) setState(result)
    } catch {
      if (current === generation.current) {
        setUncertain(true); setError('Change outcome uncertain. Reload to inspect saved state; do not repeat the action.')
      }
    } finally { active.current = false; setBusy(false); resetConsent() }
  }

  const close = () => { generation.current++; setOpen(false); resetConsent(); trigger.current?.focus() }
  const keys = event => {
    if (event.key === 'Escape') { event.preventDefault(); close() }
    if (event.key !== 'Tab') return
    const nodes = panel.current?.querySelectorAll('button:not(:disabled),select:not(:disabled),input:not(:disabled),a[href]')
    if (!nodes?.length) return
    const first = nodes[0], last = nodes[nodes.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  useEffect(() => { if (open) panel.current?.querySelector('button')?.focus() }, [open])

  return <>
    <button ref={trigger} type="button" className={button} onClick={() => { setOpen(true); void load() }}>Handoff scope</button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3">
      <section ref={panel} role="dialog" aria-modal="true" aria-labelledby="pixel-scope-title" onKeyDown={keys}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-theme-border bg-theme-bg p-4 text-theme-text">
        <div className="flex items-center justify-between gap-3"><h2 id="pixel-scope-title" className="text-lg font-semibold">Choose handoff scope</h2>
          <button type="button" className={button} onClick={close}>Close scope controls</button></div>
        <p className="my-3 text-sm">These are saved preferences, not an active route. They do not start inference, install routing, change the tool computer or grant privileges. Once routing is activated, each selected handoff run still waits for Review handoffs approval.</p>
        <button type="button" className={button} disabled={busy} onClick={() => { void load() }}>Reload preferences</button>
        {error && <p role="alert" className="my-3 text-red-400">{error}</p>}
        {sending && <p className="my-3 text-sm">Current work retains its frozen route. Wait for it to finish before changing preferences here.</p>}
        {state && <div className="mt-3 space-y-3 break-words text-sm">
          <p>Conversation: {chatId}<br />Task: {state.taskId || 'No explicit task begun'}<br />
            Selected preference: {state.effectiveSelection?.providerId || 'Configured leader'}{state.effectiveScope ? ` (${labels[state.effectiveScope]})` : ''}.<br />
            New-task default: {state.defaultSelection?.providerId || 'Configured leader'}.</p>
          <p>A task can span multiple messages and runtime runs. Begin it explicitly, then End task when finished. New-task defaults are captured at Begin task; changing a default does not alter existing tasks.</p>
          <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={!canChange || !!state.taskId} onClick={() => { void mutate('begin') }}>Begin task</button>
            <button type="button" className={button} disabled={!canChange || !state.taskId} onClick={() => { void mutate('end') }}>End task</button></div>
          <label className="block">Preference scope<select aria-label="Preference scope" value={scope} disabled={busy}
            className="my-2 w-full rounded border border-theme-border bg-theme-bg p-2" onChange={event => { setScope(event.target.value); resetConsent() }}>
            {scopes.map(item => <option key={item} value={item}>{labels[item]}</option>)}</select></label>
          <p>Handoff recipient: {target ? `${target.label} (${target.kind}) · ${target.model}` : 'Configure a handoff recipient in Settings first.'}<br />
            {target?.baseUrl} {target && `· Provider revision ${configuration.revision}`}</p>
          <p>Task choices override conversation choices, which override the task&apos;s captured default. Return clears a task or conversation choice; the next lower layer or configured leader resumes on the next run. Resetting the new-task default affects future tasks only. End task clears its choice and captured default, but keeps the conversation choice.</p>
          <label className="flex gap-2"><input type="checkbox" checked={reviewed} disabled={!canChange} onChange={event => setReviewed(event.target.checked)} />I reviewed this recipient, duration and return behavior</label>
          {target?.kind === 'cloud' && <>
            <label className="flex gap-2"><input type="checkbox" checked={cloud} disabled={!canChange} onChange={event => setCloud(event.target.checked)} />Allow cloud conversation and tool-result transfer for this preference, subject to each run&apos;s checkpoint approval</label>
            <label className="flex gap-2"><input type="checkbox" checked={cost} disabled={!canChange} onChange={event => setCost(event.target.checked)} />Accept unknown provider cost for each separately approved bounded run</label>
          </>}
          <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={!canSelect} onClick={() => { void mutate('select') }}>Save handoff preference</button>
            <button type="button" className={button} disabled={!canChange || scope === 'task' && !state.taskId} onClick={() => { void mutate('return') }}>{scope === 'default' ? 'Reset new-task default' : 'Return from selected scope'}</button></div>
        </div>}
      </section>
    </div>}
  </>
}
