import { useCallback, useEffect, useRef, useState } from 'react'
import { copy, createProvider, eligible, prepareSave, readConfiguration } from './pixelProviderForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-blue-500'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500'
const numeric = value => value === '' ? '' : Number(value)

function Field({ label, children }) {
  return <label className="flex min-w-0 flex-col gap-1 text-sm text-theme-text-muted">{label}{children}</label>
}
function Toggle({ label, ...props }) {
  return <label className="flex items-center gap-2 text-sm"><input type="checkbox" {...props} />{label}</label>
}

export default function PixelProviderSettings({ showHeading = true }) {
  const [snapshot, setSnapshot] = useState(null)
  const [draft, setDraft] = useState(null)
  const [secrets, setSecrets] = useState({})
  const [removals, setRemovals] = useState({})
  const [newId, setNewId] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)
  const sequence = useRef(0)
  const controller = useRef(null)
  const writePending = useRef(false)

  const request = useCallback(async (method, payload) => {
    const seq = ++sequence.current
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    const timer = setTimeout(() => abort.abort(), 10000)
    const current = () => mounted.current && sequence.current === seq
    try {
      const response = await fetch('/api/pixel/providers' + (method === 'POST' ? '/save' : ''), {
        method, signal: abort.signal,
        ...(payload ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}),
      })
      if (!current()) return
      if (!response.ok) {
        if (method === 'POST') {
          setStale(response.status === 409 || response.status >= 500)
          setError(response.status === 409 ? 'Settings changed elsewhere. Reload before saving.'
            : 'Settings could not be saved. Reload to check the stored state; re-enter any unsaved key.')
        } else setError('Provider settings are unavailable. Reload to try again.')
        return
      }
      const value = readConfiguration(await response.json(), payload ? payload.expectedRevision + 1 : undefined)
      if (!current()) return
      setSnapshot(value)
      setDraft(copy(value))
      setSecrets({})
      setRemovals({})
      setDirty(false)
      setStale(false)
      setNewId('')
      setNewLabel('')
      if (method === 'POST') setNotice('Settings saved. Pixel runtime has not been changed.')
    } catch {
      if (!current()) return
      if (method === 'POST') setStale(true)
      setError(method === 'POST'
        ? 'Save result is unknown. Reload before saving again; re-enter any unsaved key.'
        : 'Provider settings are unavailable. Reload to try again.')
    } finally {
      clearTimeout(timer)
      if (current()) {
        setLoading(false)
        setSaving(false)
        writePending.current = false
      }
    }
  }, [])

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    setNotice('')
    return request('GET')
  }, [request])

  useEffect(() => {
    mounted.current = true
    load()
    return () => { mounted.current = false; sequence.current++; controller.current?.abort() }
  }, [load])

  const edit = mutate => {
    setDraft(value => { const next = copy(value); mutate(next); return next })
    setDirty(true)
    setError('')
    setNotice('')
  }
  const providerEdit = (id, key, value) => edit(next => { next.providers.find(p => p.id === id)[key] = value })
  const reload = () => {
    if (!writePending.current && (!dirty || window.confirm('Discard unsaved Pixel provider edits and reload?'))) load()
  }
  const save = () => {
    if (writePending.current || saving || loading || stale || !dirty) return
    let payload
    try { payload = prepareSave(draft, snapshot, secrets, removals) }
    catch (problem) { setError(problem.message); return }
    writePending.current = true
    setSaving(true)
    setError('')
    setNotice('')
    setSecrets({}) // Write-only fields are cleared even when a request fails.
    setRemovals({})
    request('POST', payload)
  }
  const add = () => {
    try {
      const provider = createProvider(newId, newLabel, draft.providers)
      edit(next => { next.providers.push(provider) })
      setNewId('')
      setNewLabel('')
    } catch (problem) { setError(problem.message) }
  }
  const remove = id => {
    edit(next => {
      next.providers = next.providers.filter(p => p.id !== id)
      for (const role of ['leader', 'advisor', 'handoff']) if (next.roles[role] === id) next.roles[role] = null
      next.roles.backups = next.roles.backups.filter(item => item !== id)
    })
    setSecrets(values => { const next = { ...values }; delete next[id]; return next })
    setRemovals(values => { const next = { ...values }; delete next[id]; return next })
  }
  const changeRole = (role, id) => {
    const removed = role === 'leader' && draft.roles.backups.includes(id)
    edit(next => {
      next.roles[role] = id || null
      if (role === 'leader') next.roles.backups = next.roles.backups.filter(item => item !== id)
    })
    if (removed) setNotice('The new leader was removed from the backup list.')
  }

  return <section aria-labelledby="pixel-connections-title" className="settings-premium-card rounded-lg border border-theme-border p-5 space-y-5 text-theme-text">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 id="pixel-connections-title" className={showHeading ? 'text-lg font-semibold' : 'sr-only'}>Pixel connections</h2>
        <p className="text-sm text-theme-text-muted">Choose inference providers without changing other ODS apps.</p></div>
      <div className="flex flex-wrap gap-2">
        <button className={buttonStyle} disabled={loading || saving} onClick={reload}>Reload providers</button>
        <button className={buttonStyle} disabled={!dirty || loading || saving} onClick={() => {
          setDraft(copy(snapshot)); setSecrets({}); setRemovals({}); setDirty(false); setError(''); setNotice('')
        }}>Cancel provider edits</button>
        <button className={buttonStyle + ' bg-blue-600 text-white'} disabled={!dirty || stale || saving || loading} onClick={save}>{saving ? 'Saving providers…' : 'Save providers'}</button>
      </div>
    </div>
    <p className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm">Saved settings are not applied to Pixel yet.</p>
    <p className="text-sm text-theme-text-muted">Choosing Tower2 inference does not move this device’s tools there. Full Access is not available through these controls.</p>
    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    {notice && <p role="status" className="text-sm text-emerald-400">{notice}</p>}
    {stale && <p className="text-sm text-amber-400">Reload the stored configuration before another save.</p>}
    {loading && <p role="status">Loading provider settings…</p>}
    {draft && <fieldset disabled={loading || saving} className="min-w-0 space-y-5">
      <legend className="sr-only">Pixel provider configuration</legend>
      <div className="flex flex-wrap gap-5">
        <Toggle label="Enable desired Pixel routing" checked={draft.enabled} onChange={e => edit(next => { next.enabled = e.target.checked })} />
        <Toggle label="Allow cloud inference" checked={draft.policy.allowCloud} onChange={e => edit(next => { next.policy.allowCloud = e.target.checked })} />
      </div>
      <p className="text-xs text-theme-text-muted">Cloud routes can send conversation content to an external provider and incur charges. Opt-in is required before activation.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Maximum attempts"><input className={inputStyle} type="number" min="1" max="9" value={draft.policy.maxAttempts} onChange={e => edit(next => { next.policy.maxAttempts = numeric(e.target.value) })} /></Field>
        <Field label="Overall deadline (seconds)"><input className={inputStyle} type="number" min="1" max="3600" value={draft.policy.deadlineSeconds} onChange={e => edit(next => { next.policy.deadlineSeconds = numeric(e.target.value) })} /></Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {['leader', 'advisor', 'handoff'].map(role => <Field key={role} label={{ leader: 'Leader', advisor: 'Escalation advisor', handoff: 'Escalation handoff' }[role]}>
          <select className={inputStyle} value={draft.roles[role] || ''} onChange={e => changeRole(role, e.target.value)}>
            <option value="">None</option>
            {draft.providers.filter(p => eligible(p, draft.policy) || p.id === draft.roles[role]).map(p => <option key={p.id} value={p.id} disabled={!eligible(p, draft.policy)}>{p.label}{eligible(p, draft.policy) ? '' : ' (not eligible)'}</option>)}
          </select>
        </Field>)}
      </div>
      <fieldset className="space-y-2"><legend className="mb-2 text-sm font-medium">Ordered backups</legend>
        <ol className="space-y-2">{draft.roles.backups.map((id, index) => {
          const provider = draft.providers.find(p => p.id === id)
          return <li key={id} className="flex flex-wrap items-center gap-2 text-sm">
            <span>{index + 1}. {provider.label}{eligible(provider, draft.policy) ? '' : ' (not eligible)'}</span>
            {[-1, 1].map(direction => <button key={direction} className={buttonStyle} aria-label={`${direction < 0 ? 'Move up' : 'Move down'} ${provider.label}`} disabled={index + direction < 0 || index + direction >= draft.roles.backups.length} onClick={() => edit(next => {
              const target = index + direction; [next.roles.backups[index], next.roles.backups[target]] = [next.roles.backups[target], next.roles.backups[index]]
            })}>{direction < 0 ? '↑' : '↓'}</button>)}
            <button className={buttonStyle} onClick={() => edit(next => { next.roles.backups = next.roles.backups.filter(item => item !== id) })}>Remove backup {provider.label}</button>
          </li>
        })}</ol>
        {!draft.roles.backups.length && <p className="text-sm text-theme-text-muted">No backups selected.</p>}
        <div className="flex flex-wrap gap-3">{draft.providers.filter(p => eligible(p, draft.policy) && p.id !== draft.roles.leader && !draft.roles.backups.includes(p.id)).map(p => <button key={p.id} className={buttonStyle} disabled={draft.roles.backups.length >= 8} onClick={() => edit(next => { next.roles.backups.push(p.id) })}>Add backup {p.label}</button>)}</div>
      </fieldset>
      <div className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
        <Field label="New provider ID"><input className={inputStyle} value={newId} maxLength={64} onChange={e => setNewId(e.target.value)} /></Field>
        <Field label="New provider label"><input className={inputStyle} value={newLabel} maxLength={256} onChange={e => setNewLabel(e.target.value)} /></Field>
        <button className={buttonStyle} disabled={draft.providers.length >= 32} onClick={add}>Add provider</button>
      </div>
      {!draft.providers.length && <p className="text-sm text-theme-text-muted">No providers configured.</p>}
      {draft.providers.map(p => <fieldset key={p.id} className="min-w-0 rounded border border-theme-border p-4 space-y-4">
        <legend className="px-2 font-medium">{p.label} ({p.id})</legend>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Toggle label="Provider enabled" checked={p.enabled} onChange={e => providerEdit(p.id, 'enabled', e.target.checked)} />
          <button className={buttonStyle} onClick={() => remove(p.id)}>Delete provider {p.label}</button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Label"><input className={inputStyle} value={p.label} maxLength={256} onChange={e => providerEdit(p.id, 'label', e.target.value)} /></Field>
          <Field label="Kind"><select className={inputStyle} value={p.kind} onChange={e => providerEdit(p.id, 'kind', e.target.value)}><option value="local">Local endpoint</option><option value="ods-peer">ODS peer</option><option value="cloud">Cloud</option></select></Field>
          <Field label="Base URL"><input className={inputStyle} value={p.baseUrl} maxLength={2048} onChange={e => providerEdit(p.id, 'baseUrl', e.target.value)} /></Field>
          <Field label="Model"><input className={inputStyle} value={p.model} maxLength={256} onChange={e => providerEdit(p.id, 'model', e.target.value)} /></Field>
          <Field label="Context tokens"><input className={inputStyle} type="number" value={p.contextTokens} onChange={e => providerEdit(p.id, 'contextTokens', numeric(e.target.value))} /></Field>
          <Field label="Maximum output tokens"><input className={inputStyle} type="number" value={p.maxOutputTokens} onChange={e => providerEdit(p.id, 'maxOutputTokens', numeric(e.target.value))} /></Field>
        </div>
        <div className="flex flex-wrap gap-4">{[['supportsTools', 'Tool calling'], ['supportsVision', 'Vision'], ['reasoning', 'Reasoning']].map(([key, label]) => <Toggle key={key} label={label} checked={p[key]} onChange={e => providerEdit(p.id, key, e.target.checked)} />)}</div>
        <p className="text-xs text-theme-text-muted">{p.hasCredential ? 'Key stored (not yet tested).' : 'No key stored.'} Blank keeps the existing key. Removing a key here does not revoke it at its provider.</p>
        <Field label="API key (write-only)"><input className={inputStyle} type="password" autoComplete="new-password" maxLength={8192} value={secrets[p.id] || ''} onChange={e => {
          setSecrets(values => ({ ...values, [p.id]: e.target.value })); setRemovals(values => ({ ...values, [p.id]: false })); setDirty(true); setNotice('')
        }} /></Field>
        {p.hasCredential && <Toggle label="Remove stored key" checked={Boolean(removals[p.id])} onChange={e => {
          setRemovals(values => ({ ...values, [p.id]: e.target.checked })); setSecrets(values => ({ ...values, [p.id]: '' })); setDirty(true); setNotice('')
        }} />}
      </fieldset>)}
    </fieldset>}
  </section>
}
