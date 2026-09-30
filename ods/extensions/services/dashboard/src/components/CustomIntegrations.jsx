import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { formatCheckedAt } from '../lib/checkedAt'

// Owner-declared systems that ODS does not run (hosted APIs, data engines,
// custom services). The API checks each health URL with one plain GET and
// returns only the outcome; see dashboard-api/custom_integrations.py.
const ENDPOINT = '/api/integrations/custom'
const POLL_INTERVAL = 30000
const REQUEST_TIMEOUT = 20000

export const STATUS_LABELS = {
  healthy: 'Healthy',
  degraded: 'Needs attention',
  unhealthy: 'Unhealthy',
  down: 'Not reachable',
  unknown: 'Not checked yet',
}

const STATUS_DOTS = {
  healthy: 'bg-green-400',
  degraded: 'bg-theme-text-secondary',
  unhealthy: 'bg-red-400',
  down: 'bg-red-400',
  unknown: 'bg-zinc-500',
}

function displayUrl(url) {
  try {
    const parsed = new URL(url)
    return `${parsed.host}${parsed.pathname === '/' ? '' : parsed.pathname}`
  } catch {
    return url
  }
}

async function request(url, { signal, ...init } = {}) {
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, REQUEST_TIMEOUT)
  const cancel = () => controller.abort()
  signal?.addEventListener('abort', cancel, { once: true })
  try {
    const response = await fetch(url, { ...init, signal: controller.signal })
    const text = await response.text()
    let body = null
    try { body = text ? JSON.parse(text) : null } catch { body = null }
    if (!response.ok) {
      const detail = typeof body?.detail === 'string' ? body.detail : `Request failed (HTTP ${response.status})`
      throw new Error(detail)
    }
    return body
  } catch (error) {
    if (timedOut) throw new Error('The integrations request timed out.')
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}

function IntegrationRow({ item, busy, onCheck, onRemove }) {
  const [confirming, setConfirming] = useState(false)
  const check = item.check
  const status = check?.status || 'unknown'
  const facts = [check?.httpStatus ? `HTTP ${check.httpStatus}` : null, Number.isFinite(check?.latencyMs) ? `${check.latencyMs} ms` : null].filter(Boolean)
  return (
    <li className="custom-integration" aria-label={`${item.name}: ${STATUS_LABELS[status] || status}`}>
      <div className="custom-integration-main">
        <span className="integration-name">
          <span className={`integration-dot ${STATUS_DOTS[status] || STATUS_DOTS.unknown}`} aria-hidden="true" />
          <strong>{item.name}</strong>
        </span>
        <span className={`custom-integration-status is-${status}`}>{STATUS_LABELS[status] || status}</span>
      </div>
      <p className="custom-integration-url" title={item.url}>{displayUrl(item.url)}</p>
      {item.notes && <p className="custom-integration-notes">{item.notes}</p>}
      {check?.detail && status !== 'healthy' && <p className="custom-integration-detail">{check.detail}</p>}
      <div className="custom-integration-footer">
        <span title={check?.checkedAt || undefined}>{formatCheckedAt(check?.checkedAt)}{facts.length > 0 && ` · ${facts.join(' · ')}`}</span>
        <span className="custom-integration-actions">
          {confirming ? <>
            <button type="button" className="is-danger" disabled={busy} onClick={() => onRemove(item)}>Remove {item.name}</button>
            <button type="button" onClick={() => setConfirming(false)}>Keep</button>
          </> : <>
            <button type="button" disabled={busy} onClick={() => onCheck(item)} aria-label={`Check ${item.name} now`}><RefreshCw size={13} aria-hidden="true" />Check now</button>
            <button type="button" disabled={busy} onClick={() => setConfirming(true)} aria-label={`Remove ${item.name}`}><Trash2 size={13} aria-hidden="true" />Remove</button>
          </>}
        </span>
      </div>
    </li>
  )
}

function AddIntegrationForm({ onAdded, onCancel }) {
  const helpId = useId()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  async function submit(event) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const body = await request(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, url, notes }),
      })
      onAdded(body.integration)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }
  return (
    <form className="custom-integration-form" onSubmit={submit} aria-label="Add an integration">
      <label>
        <span>Name</span>
        <input required maxLength={60} value={name} onChange={event => setName(event.target.value)} placeholder="Jev decision API" autoComplete="off" />
      </label>
      <div className="custom-integration-field">
        <label>
          <span>Health URL</span>
          <input required type="url" maxLength={512} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://jev.example.com/health" autoComplete="off" spellCheck={false} inputMode="url" aria-describedby={helpId} />
        </label>
        <small id={helpId}>An address that answers a plain GET without a key. For something running on this computer, use <code>http://host.docker.internal:PORT/…</code> instead of <code>localhost</code>.</small>
      </div>
      <label>
        <span>Notes <em>(optional)</em></span>
        <input maxLength={280} value={notes} onChange={event => setNotes(event.target.value)} placeholder="What it does or who owns it" autoComplete="off" />
      </label>
      {error && <p role="alert" className="custom-integration-error">{error}</p>}
      <div className="custom-integration-form-actions">
        <button type="submit" className="is-primary" disabled={saving}>{saving ? 'Adding and checking…' : 'Add and check'}</button>
        <button type="button" onClick={onCancel} disabled={saving}>Cancel</button>
      </div>
    </form>
  )
}

export default function CustomIntegrations({ onChange }) {
  const [items, setItems] = useState([])
  const [policy, setPolicy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [adding, setAdding] = useState(false)
  const [busyId, setBusyId] = useState(null)
  const [, setTick] = useState(0)
  const active = useRef(null)
  const addButton = useRef(null)

  const load = useCallback(async () => {
    if (document.hidden || active.current) return
    const controller = new AbortController()
    active.current = controller
    try {
      const body = await request(ENDPOINT, { signal: controller.signal })
      if (active.current !== controller) return
      setItems(Array.isArray(body?.integrations) ? body.integrations : [])
      setPolicy(body?.policy || null)
      setError(null)
    } catch (err) {
      if (active.current === controller) setError(err.message)
    } finally {
      if (active.current === controller) {
        active.current = null
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    load()
    const poll = setInterval(load, POLL_INTERVAL)
    const clock = setInterval(() => setTick(value => value + 1), 15000)
    const onVisible = () => { if (!document.hidden) load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      const pending = active.current
      active.current = null
      pending?.abort()
      clearInterval(poll)
      clearInterval(clock)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  useEffect(() => { onChange?.(items) }, [items, onChange])

  function replace(item) {
    setItems(previous => previous.some(entry => entry.id === item.id)
      ? previous.map(entry => entry.id === item.id ? item : entry)
      : [...previous, item])
  }

  async function check(item) {
    setBusyId(item.id)
    setNotice(null)
    try {
      const body = await request(`${ENDPOINT}/${encodeURIComponent(item.id)}/check`, { method: 'POST' })
      replace(body.integration)
    } catch (err) {
      setNotice(err.message)
    } finally {
      setBusyId(null)
    }
  }

  async function remove(item) {
    setBusyId(item.id)
    setNotice(null)
    try {
      await request(`${ENDPOINT}/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
      setItems(previous => previous.filter(entry => entry.id !== item.id))
      setNotice(`Removed ${item.name}. ODS no longer checks it.`)
    } catch (err) {
      setNotice(err.message)
    } finally {
      setBusyId(null)
    }
  }

  const maximum = policy?.maximum || 25
  return (
    <section className="integrations-section" aria-labelledby="custom-integrations-heading">
      <div className="integrations-section-heading">
        <div>
          <h2 id="custom-integrations-heading">Your integrations</h2>
          <p>Systems ODS does not run but you rely on — hosted decision APIs, data engines, custom services. ODS checks each one while this page is open.</p>
        </div>
        {!adding && <button ref={addButton} type="button" className="integrations-action" onClick={() => { setAdding(true); setNotice(null) }} disabled={items.length >= maximum}><Plus size={14} aria-hidden="true" />Add integration</button>}
      </div>
      {adding && <AddIntegrationForm onCancel={() => setAdding(false)} onAdded={item => {
        replace(item)
        setAdding(false)
        setNotice(`Added ${item.name}. ${item.check?.status === 'healthy' ? 'It answered the first check.' : 'Its first check did not succeed — see the details below.'}`)
      }} />}
      {notice && <p role="status" className="integrations-notice"><span>{notice}</span><button type="button" aria-label="Dismiss message" onClick={() => setNotice(null)}><X size={13} aria-hidden="true" /></button></p>}
      {loading ? <p role="status" className="integrations-empty">Loading your integrations…</p>
        : error && !items.length ? <p role="alert" className="custom-integration-error">Your integrations could not be loaded. {error}<button type="button" onClick={load}>Retry</button></p>
          : items.length === 0 ? <div className="integrations-empty-state">
            <p>No integrations yet.</p>
            <span>Add one to see whether an external system is answering, when ODS last checked it, and how long it took.</span>
          </div>
            : <>
              {error && <p role="alert" className="custom-integration-error">Status could not be refreshed. {error}</p>}
              <ul className="custom-integration-list">{items.map(item => <IntegrationRow key={item.id} item={item} busy={busyId === item.id} onCheck={check} onRemove={remove} />)}</ul>
            </>}
      <details className="integrations-help">
        <summary>How checks work</summary>
        <ul>
          <li>ODS sends one plain <code>GET</code> to the health URL, waits up to {policy?.timeoutSeconds || 5} seconds and does not follow redirects. It re-checks at most once a minute, and only while someone has this page open.</li>
          <li>Any 2xx answer is <strong>healthy</strong>. A redirect or a 401/403 means the system is reachable but the URL needs attention. Other codes are <strong>unhealthy</strong>; no answer is <strong>not reachable</strong>.</li>
          <li>Nothing but the request is sent: no keys, cookies or passwords. ODS never reads or keeps the response body, so URLs with user names, passwords or query strings are refused. Choose an endpoint that answers without credentials.</li>
          <li>The list is saved in the ODS data folder (<code>integrations/custom.json</code>) and holds up to {maximum} entries. Removing an entry only stops the checks; it never touches the other system.</li>
          <li>Services with an ODS extension manifest are checked by ODS itself and appear under ODS services below.</li>
        </ul>
      </details>
    </section>
  )
}
