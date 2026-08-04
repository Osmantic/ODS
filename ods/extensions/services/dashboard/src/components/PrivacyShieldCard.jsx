import { Loader2, Shield, ShieldOff } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

// Auth: nginx injects the dashboard API key for /api/ requests, so relative
// URLs need no explicit header here (see Extensions.jsx).
const fetchJson = async (url, options = {}, ms = 8000) => {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), ms)
  try {
    return await fetch(url, { ...options, signal: c.signal })
  } finally {
    clearTimeout(t)
  }
}

// /api/privacy-shield/stats answers 200 with an {error} body when the shield
// is unreachable or SHIELD_API_KEY is unset, so "no stats" is a normal state.
const readStats = (payload) => (
  payload && !payload.error && typeof payload.total_pii_scrubbed === 'number' ? payload : null
)

export default function PrivacyShieldCard({ deployed = false }) {
  const [status, setStatus] = useState(null)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!deployed) return
    try {
      const [statusRes, statsRes] = await Promise.all([
        fetchJson('/api/privacy-shield/status'),
        fetchJson('/api/privacy-shield/stats'),
      ])
      if (!statusRes.ok) throw new Error(`Privacy Shield status unavailable (${statusRes.status})`)
      setStatus(await statusRes.json())
      setStats(statsRes.ok ? readStats(await statsRes.json()) : null)
      setError(null)
    } catch (err) {
      setError(err?.name === 'AbortError' ? 'Privacy Shield status timed out' : (err?.message || 'Failed to read Privacy Shield status'))
    }
  }, [deployed])

  useEffect(() => { load() }, [load])

  if (!deployed) return null

  const enabled = Boolean(status?.enabled)

  const toggle = async () => {
    setBusy(true)
    setNotice(null)
    try {
      const response = await fetchJson(
        '/api/privacy-shield/toggle',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enable: !enabled }),
        },
        35000,
      )
      const payload = await response.json().catch(() => ({}))
      // The endpoint reports failures in the body with a 200, so success is
      // the payload's own flag rather than the HTTP status.
      setNotice({
        tone: payload?.success ? 'ok' : 'error',
        text: payload?.message || (payload?.success ? 'Privacy Shield updated' : 'Privacy Shield could not be updated'),
      })
      await load()
    } catch (err) {
      setNotice({ tone: 'error', text: err?.message || 'Privacy Shield could not be updated' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-10 rounded-lg border border-theme-border bg-theme-card p-5" data-testid="privacy-shield-card">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          {enabled
            ? <Shield size={20} className="text-emerald-400" />
            : <ShieldOff size={20} className="text-theme-text-muted" />}
          <div>
            <h2 className="text-sm font-semibold text-theme-text">Privacy Shield</h2>
            <p className="text-xs text-theme-text-muted">
              {status?.message || 'PII is scrubbed before prompts leave this machine.'}
            </p>
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={toggle}
          className={`shrink-0 rounded-lg px-4 py-2 text-sm disabled:opacity-50 ${
            enabled
              ? 'border border-theme-border text-theme-text hover:border-red-400/50 hover:text-red-300'
              : 'bg-theme-accent text-white'
          }`}
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : (enabled ? 'Stop' : 'Start')}
        </button>
      </div>

      {error && <p className="mb-3 text-xs text-red-300">{error}</p>}
      {notice && (
        <p className={`mb-3 text-xs ${notice.tone === 'ok' ? 'text-emerald-300' : 'text-red-300'}`}>
          {notice.text}
        </p>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label="PII scrubbed" value={stats ? stats.total_pii_scrubbed : '—'} />
        <Metric label="Active sessions" value={stats ? stats.active_sessions : '—'} />
        <Metric label="Cache" value={stats ? (stats.cache_enabled ? `On (${stats.cache_size})` : 'Off') : '—'} />
        <Metric label="Port" value={status?.port || '—'} />
      </div>
    </div>
  )
}

function Metric({ label, value }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.18em] text-theme-text-muted">{label}</p>
      <p className="mt-1 text-lg font-semibold text-theme-text">{value}</p>
    </div>
  )
}
