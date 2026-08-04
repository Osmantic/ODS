import {
  AlertCircle, Bot, Brain, Check, Code, Database, FileText, Image, Loader2,
  MessageSquare, Mic, RefreshCw, Search, Shield, Trash2, Workflow, Zap,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

// Auth: nginx injects "Authorization: Bearer ${DASHBOARD_API_KEY}" for /api/
// requests, so relative URLs need no explicit auth here (see Extensions.jsx).
const fetchJson = async (url, options = {}, ms = 8000) => {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), ms)
  try {
    return await fetch(url, { ...options, signal: c.signal })
  } finally {
    clearTimeout(t)
  }
}

// Catalog entries carry an icon name; anything unknown falls back to Workflow.
const ICON_MAP = {
  Bot, Brain, Code, Database, FileText, Image, MessageSquare, Mic,
  Search, Shield, Workflow, Zap,
}

const STATUS_STYLES = {
  active: 'bg-green-500/20 text-green-400',
  installed: 'bg-blue-500/20 text-blue-400',
  available: 'border border-theme-border text-theme-text-muted',
}

const STATUS_LABELS = {
  active: 'Active',
  installed: 'Installed',
  available: 'Available',
}

export default function Workflows() {
  const [catalog, setCatalog] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [category, setCategory] = useState('all')

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true)
    try {
      const response = await fetchJson('/api/workflows')
      if (!response.ok) throw new Error(`Workflow catalog unavailable (${response.status})`)
      setCatalog(await response.json())
      setError(null)
    } catch (err) {
      setError(err?.name === 'AbortError' ? 'Workflow catalog request timed out' : (err?.message || 'Failed to load workflows'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const runAction = async (workflow, action) => {
    setBusyId(workflow.id)
    setNotice(null)
    try {
      const response = await fetchJson(`/api/workflows/${workflow.id}/${action}`, { method: 'POST' }, 30000)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status})`)
      setNotice({ tone: 'ok', text: payload?.message || `${workflow.name} updated` })
      // n8n owns the truth about what is installed; re-read instead of guessing.
      await load({ quiet: true })
    } catch (err) {
      setNotice({ tone: 'error', text: err?.message || `Could not ${action} ${workflow.name}` })
    } finally {
      setBusyId(null)
    }
  }

  const workflows = catalog?.workflows || []
  const categories = catalog?.categories || {}
  const visible = category === 'all' ? workflows : workflows.filter(w => w.category === category)

  return (
    <div className="p-8">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-theme-text">Workflows</h1>
          <p className="mt-1 text-sm text-theme-text-muted">
            Prebuilt n8n automations shipped with ODS. Installing one imports it into n8n and activates it.
          </p>
        </div>
        <button
          type="button"
          onClick={() => load()}
          className="flex items-center gap-2 rounded-lg border border-theme-border bg-theme-card px-4 py-2 text-sm text-theme-text hover:border-theme-accent/50"
        >
          <RefreshCw size={15} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {notice && (
        <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${
          notice.tone === 'ok'
            ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-300'
            : 'border-red-400/30 bg-red-500/10 text-red-300'
        }`}>
          {notice.text}
        </div>
      )}

      {catalog && !catalog.n8nAvailable && (
        <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          n8n is not reachable, so workflows cannot be installed yet. Enable the n8n extension and try again.
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-theme-text-muted">
          <Loader2 size={16} className="animate-spin" />
          Loading workflow catalog...
        </div>
      ) : (
        <>
          <div className="mb-5 flex flex-wrap gap-2">
            {[['all', 'All'], ...Object.entries(categories).map(([id, meta]) => [id, meta?.name || id])].map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setCategory(id)}
                className={`rounded-full border px-4 py-1.5 text-sm transition-colors ${
                  category === id
                    ? 'border-theme-accent bg-theme-accent text-white'
                    : 'border-theme-border bg-theme-card text-theme-text-muted hover:text-theme-text'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {visible.length === 0 ? (
            <p className="text-sm text-theme-text-muted">No workflows in this category.</p>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {visible.map(workflow => {
                const Icon = ICON_MAP[workflow.icon] || Workflow
                const blockedBy = Object.entries(workflow.dependencyStatus || {})
                  .filter(([, ok]) => !ok)
                  .map(([dep]) => dep)
                const busy = busyId === workflow.id
                const canInstall = workflow.allDependenciesMet && catalog?.n8nAvailable

                return (
                  <div key={workflow.id} className="flex flex-col rounded-lg border border-theme-border bg-theme-card p-5">
                    <div className="mb-3 flex items-start justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <Icon size={20} className="text-theme-accent-light" />
                        <h2 className="text-sm font-semibold text-theme-text">{workflow.name}</h2>
                      </div>
                      <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs ${STATUS_STYLES[workflow.status] || STATUS_STYLES.available}`}>
                        {STATUS_LABELS[workflow.status] || workflow.status}
                      </span>
                    </div>

                    <p className="mb-3 text-sm text-theme-text-muted">{workflow.description}</p>

                    <div className="mb-4 flex flex-wrap gap-2 text-xs text-theme-text-muted">
                      <span className="rounded border border-theme-border px-2 py-0.5">
                        {categories[workflow.category]?.name || workflow.category}
                      </span>
                      {workflow.setupTime && (
                        <span className="rounded border border-theme-border px-2 py-0.5">Setup {workflow.setupTime}</span>
                      )}
                      {workflow.installed && workflow.executions > 0 && (
                        <span className="rounded border border-theme-border px-2 py-0.5">{workflow.executions} runs</span>
                      )}
                    </div>

                    {blockedBy.length > 0 && (
                      <p className="mb-3 text-xs text-amber-300">
                        Needs {blockedBy.join(', ')} running first.
                      </p>
                    )}

                    <div className="mt-auto">
                      {workflow.installed ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => runAction(workflow, 'disable')}
                          className="flex w-full items-center justify-center gap-2 rounded-lg border border-theme-border px-4 py-2 text-sm text-theme-text hover:border-red-400/50 hover:text-red-300 disabled:opacity-50"
                        >
                          {busy ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                          Remove
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled={busy || !canInstall}
                          onClick={() => runAction(workflow, 'enable')}
                          className="flex w-full items-center justify-center gap-2 rounded-lg bg-theme-accent px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                          Install
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
