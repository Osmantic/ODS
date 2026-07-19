import {
  Database, Cpu, Workflow, Plug, Image, MessageSquare, Code,
  FileText, Shield, Globe, Music, Video, Search, Puzzle,
  Box, Loader2, RefreshCw, ChevronDown, ChevronUp, Package, Info, X, Download, Trash2, ExternalLink, Terminal, Copy, Check,
} from 'lucide-react'
import { useState, useEffect, useRef } from 'react'
import { DependencyBadges, DependencyConfirmDialog, DisableDependentWarning } from '../components/DependencyBadges'
import { TemplatePicker } from '../components/TemplatePicker'
import { getTemplateStatus } from '../lib/templates'
import { createRecoveryTracker } from '../utils/recoveryTracker'

// Re-export so existing importers of getTemplateStatus from this module keep working.
export { getTemplateStatus }

// API/backend services with no user-facing web UI — show badge instead of port link.
const HEADLESS_EXTENSIONS = new Set(['embeddings', 'tts', 'whisper', 'privacy-shield'])

// Auth: nginx injects "Authorization: Bearer ${DASHBOARD_API_KEY}" via
// proxy_set_header for all /api/ requests (see nginx.conf).  All fetches
// use relative URLs so they route through the nginx proxy which adds the
// header before forwarding to dashboard-api.  No explicit auth in JS.

const fetchJson = async (url, ms = 8000) => {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), ms)
  try {
    return await fetch(url, { signal: c.signal })
  } finally {
    clearTimeout(t)
  }
}

const ICON_MAP = {
  Database, Cpu, Workflow, Plug, Image, MessageSquare, Code,
  FileText, Shield, Globe, Music, Video, Search, Puzzle, Box,
}

const friendlyError = (detail) => {
  if (!detail || typeof detail !== 'string') return detail
  if (detail.includes('build context') || detail.includes('local build'))
    return 'This extension requires a local build and cannot be installed through the portal yet.'
  if (detail.includes('already installed'))
    return 'This extension is already installed.'
  if (detail.includes('already enabled'))
    return 'This extension is already enabled.'
  if (detail.includes('already disabled'))
    return 'This extension is already disabled.'
  if (detail.includes('Disable extension before'))
    return 'Please disable this extension before removing it.'
  if (detail.includes('still enabled'))
    return 'Please disable this extension before purging its data.'
  if (detail.includes('No data directory'))
    return 'No data directory found for this extension.'
  if (detail.includes('Missing dependencies'))
    return detail
  return detail
}

const STATUS_STYLES = {
  enabled:       'bg-green-500/20 text-green-400',
  cli_installed: 'bg-green-500/20 text-green-400',
  stopped:       'bg-red-500/20 text-red-400',
  unhealthy:     'bg-amber-500/20 text-amber-400',
  disabled:      'bg-theme-border text-theme-text-muted',
  not_installed: 'border border-theme-border text-theme-text-muted',
  incompatible:  'bg-orange-500/20 text-orange-400',
  installing:    'bg-blue-500/20 text-blue-400',
  setting_up:    'bg-blue-500/20 text-blue-400',
  error:         'bg-red-500/20 text-red-300',
}

const STATUS_DESCRIPTIONS = {
  enabled:       'Service is running and healthy',
  cli_installed: 'CLI tool installed \u2014 invoke via `docker compose run --rm <service>`',
  disabled:      'Installed but turned off \u2014 won\u2019t start on restart',
  stopped:       'Enabled but container is not running',
  unhealthy:     'Container is running but health check is failing \u2014 check logs',
  not_installed: 'Available to install from the extension library',
  incompatible:  'Requires a GPU backend not available on this system',
  installing:    'Being downloaded and set up',
  setting_up:    'Running post-install configuration hooks',
  error:         'Installation or startup failed \u2014 click for details',
}

export default function Extensions() {
  const [catalog, setCatalog] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [expanded, setExpanded] = useState(null)
  const [mutating, setMutating] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [toast, setToast] = useState(null)
  const [consoleExt, setConsoleExt] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [progressMap, setProgressMap] = useState({})
  const [depConfirm, setDepConfirm] = useState(null)
  const [templates, setTemplates] = useState([])
  const [templatesOpen, setTemplatesOpen] = useState(false)
  const [pollingLost, setPollingLost] = useState(false)
  const installProgressRef = useRef(null)
  const activePollers = useRef({})
  // Per-service recovery tracker: counts consecutive fetch failures and
  // fires onThresholdReached/onRecovered to drive the polling-lost banner.
  // Keyed by serviceId because multiple installs can be polling concurrently.
  const recoveryTrackers = useRef({})

  const pollProgress = (serviceId) => {
    if (activePollers.current[serviceId]) return
    recoveryTrackers.current[serviceId] = createRecoveryTracker({
      threshold: 3,
      onThresholdReached: () => {
        setPollingLost(true)
        // Attempt to recover catalog state — if the backend is back,
        // the next successful poll will clear the banner.
        fetchCatalog()
      },
      onRecovered: () => setPollingLost(prev => (prev ? false : prev)),
    })
    activePollers.current[serviceId] = setInterval(async () => {
      try {
        const res = await fetchJson(`/api/extensions/${serviceId}/progress`)
        // Successful fetch (regardless of HTTP status) means the dashboard
        // is reachable again — reset the failure counter and clear the banner.
        recoveryTrackers.current[serviceId]?.recordSuccess()
        if (!res.ok) return
        const data = await res.json()
        if (data.status === 'idle') return
        setProgressMap(prev => ({ ...prev, [serviceId]: data }))
        if (data.status === 'error') {
          clearInterval(activePollers.current[serviceId])
          delete activePollers.current[serviceId]
          delete recoveryTrackers.current[serviceId]
          setToast({ type: 'error', text: data.error || 'Installation failed' })
          setProgressMap(prev => { const next = { ...prev }; delete next[serviceId]; return next })
          fetchCatalog()
        } else if (data.status === 'started') {
          // Container is up but healthcheck may not have passed yet.
          // Refresh catalog — if it shows "enabled" (long-running service)
          // or "cli_installed" (one-shot CLI tool whose container exits
          // after init), we're done.
          const catRes = await fetchJson('/api/extensions/catalog')
          if (!catRes.ok) return
          const catData = await catRes.json()
          setCatalog(catData)
          const ext = catData.extensions?.find(e => e.id === serviceId)
          if (ext && (ext.status === 'enabled' || ext.status === 'cli_installed')) {
            clearInterval(activePollers.current[serviceId])
            delete activePollers.current[serviceId]
            delete recoveryTrackers.current[serviceId]
            const successText = ext.status === 'cli_installed'
              ? `${ext.name || 'Extension'} installed — run via \`docker compose run --rm ${serviceId}\`.`
              : 'Extension installed and started.'
            setToast({ type: 'success', text: successText })
            setProgressMap(prev => { const next = { ...prev }; delete next[serviceId]; return next })
          }
          // If not yet "enabled" / "cli_installed", keep polling — healthcheck still running
        }
      } catch (err) {
        // Dashboard-api may be mid-restart, or the browser briefly lost
        // network. Tracker counts consecutive failures; surfaces a banner
        // after 3 via onThresholdReached so the user isn't left staring
        // at a silent spinner forever.
        console.warn('poll fetch failed:', err)
        recoveryTrackers.current[serviceId]?.recordFailure()
      }
    }, 3000)
  }

  useEffect(() => {
    fetchCatalog()
    fetch('/api/templates')
      .then(r => r.ok ? r.json() : { templates: [] })
      .then(d => setTemplates(d.templates || []))
      .catch(() => {})
    return () => {
      Object.values(activePollers.current).forEach(clearInterval)
      activePollers.current = {}
      recoveryTrackers.current = {}
    }
  }, [])

  // Start polling for installing extensions + fetch progress for error state (after page refresh)
  useEffect(() => {
    if (!catalog) return
    const installing = catalog.extensions.filter(e => e.status === 'installing' || e.status === 'setting_up')
    installing.forEach(e => pollProgress(e.id))
    // Fetch progress once for errored extensions to show the error message
    catalog.extensions.filter(e => e.status === 'error').forEach(async (e) => {
      try {
        const res = await fetchJson(`/api/extensions/${e.id}/progress`)
        if (!res.ok) return
        const data = await res.json()
        if (data.status === 'error') setProgressMap(prev => ({ ...prev, [e.id]: data }))
      } catch { /* ignore */ }
    })
  }, [catalog])

  useEffect(() => {
    if (toast && toast.type !== 'info') {
      const t = setTimeout(() => setToast(null), 8000)
      return () => clearTimeout(t)
    }
  }, [toast])

  useEffect(() => {
    if (!confirm) return
    const handler = (e) => { if (e.key === 'Escape') setConfirm(null) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [confirm])

  const fetchCatalog = async () => {
    try {
      if (!catalog) setLoading(true)
      setRefreshing(true)
      setError(null)
      const res = await fetchJson(`/api/extensions/catalog`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setCatalog(await res.json())
    } catch (err) {
      setError(err.name === 'AbortError' ? 'Request timed out' : 'Failed to load extensions catalog')
      console.error('Extensions fetch error:', err)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  const handleMutation = async (serviceId, action, { autoEnableDeps = false } = {}) => {
    setMutating(serviceId)
    setConfirm(null)
    setDepConfirm(null)
    try {
      let url = action === 'uninstall'
        ? `/api/extensions/${serviceId}`
        : action === 'purge'
        ? `/api/extensions/${serviceId}/data`
        : `/api/extensions/${serviceId}/${action}`
      if (action === 'enable' && autoEnableDeps) {
        url += '?auto_enable_deps=true'
      }
      const opts = {
        method: action === 'uninstall' || action === 'purge' ? 'DELETE' : 'POST',
        signal: AbortSignal.timeout(300000),
      }
      if (action === 'purge') {
        opts.headers = { 'Content-Type': 'application/json' }
        opts.body = JSON.stringify({ confirm: true })
      }
      const res = await fetch(url, opts)
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        const detail = err.detail
        // Handle missing dependencies response
        if (action === 'enable' && res.status === 400 && detail?.missing_dependencies) {
          const ext = extensions.find(e => e.id === serviceId)
          setMutating(null)
          setDepConfirm({ ext, missingDeps: detail.missing_dependencies })
          return
        }
        throw new Error((typeof detail === 'string' ? detail : detail?.message) || `Failed to ${action}`)
      }
      const data = await res.json()

      if (action === 'install' || action === 'enable') {
        // Refresh catalog to show "installing" state, then let the
        // catalog-driven poller handle the rest (toast + final refresh)
        await fetchCatalog()
        pollProgress(serviceId)
      } else {
        let successText = data.message || (
          action === 'uninstall' ? 'Extension removed' :
          action === 'purge' ? `Data purged — ${data.size_gb_freed ?? 0} GB freed` :
          `Extension ${action}d`
        )
        if (data.data_info) {
          successText += ` Data preserved (${data.data_info.size_gb} GB) — purge to remove.`
        }
        if (data.restart_required) {
          setToast({ type: 'info', text: `${successText} — restart needed to apply.` })
        } else {
          setToast({ type: 'success', text: successText })
        }
        await fetchCatalog()
      }
    } catch (err) {
      const base = friendlyError(err.message) || `Failed to ${action} extension`
      setToast({ type: 'error', text: base })
    } finally {
      setMutating(null)
    }
  }

  const requestAction = (ext, action) => {
    const messages = {
      install: `Install ${ext.name}? This will download and start the service.`,
      enable: `Enable ${ext.name}? The service will be started.`,
      disable: `Disable ${ext.name}? The service will be stopped.`,
      uninstall: `Remove ${ext.name}? You can reinstall it from the library.`,
      purge: `Permanently delete all data for ${ext.name}? This cannot be undone.`,
    }
    setConfirm({ action, ext, message: messages[action] })
  }

  if (loading && !catalog) {
    return (
      <div className="p-8 flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-theme-accent" size={32} />
      </div>
    )
  }

  const extensions = catalog?.extensions || []
  const summary = catalog?.summary || {}

  // Derive unique categories from features
  const categories = ['all', ...new Set(
    extensions
      .flatMap(ext => ext.features?.map(f => f.category) || [])
      .filter(Boolean)
  )]

  const STATUS_FILTERS = ['all', 'enabled', 'cli_installed', 'stopped', 'unhealthy', 'disabled', 'installing', 'setting_up', 'error', 'not_installed', 'incompatible']
  const STATUS_LABELS = { all: 'All', enabled: 'Enabled', cli_installed: 'CLI Installed', stopped: 'Stopped', unhealthy: 'Unhealthy', disabled: 'Disabled', installing: 'Installing', setting_up: 'Setting Up', error: 'Error', not_installed: 'Not Installed', incompatible: 'Incompatible' }

  // Filter extensions
  const query = search.toLowerCase()
  const filtered = extensions.filter(ext => {
    if (statusFilter !== 'all' && ext.status !== statusFilter) return false
    if (category !== 'all' && !ext.features?.some(f => f.category === category)) return false
    if (query && !ext.name.toLowerCase().includes(query) && !ext.description?.toLowerCase().includes(query)) return false
    return true
  })

  return (
    <div className="p-8">
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-theme-text">Extensions</h1>
          <p className="mt-1 text-theme-text-secondary">
            Browse and discover add-on services.
          </p>
        </div>
        <div className="liquid-metal-frame liquid-metal-frame--soft flex items-center gap-4 text-xs text-theme-text-muted font-mono bg-theme-card border border-theme-border rounded-lg px-3 py-2">
          {catalog?.agent_available !== undefined && (
            <div className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${catalog.agent_available ? 'bg-emerald-400' : 'bg-red-500'}`} />
              <span className={catalog.agent_available ? 'text-theme-text-secondary' : 'text-theme-text-muted'}>
                {catalog.agent_available ? 'Agent online' : 'Agent offline'}
              </span>
            </div>
          )}
          <button
            onClick={fetchCatalog}
            disabled={refreshing}
            className="text-theme-text-muted/65 hover:text-theme-text transition-colors disabled:opacity-50 flex items-center gap-1.5 uppercase tracking-[0.16em]"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="mb-6 rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-200">
          {error} — <button className="underline" onClick={fetchCatalog}>Retry</button>
        </div>
      )}

      {/* Summary bar */}
      <div className="bg-theme-card border border-theme-border rounded-xl p-4 mb-6 liquid-metal-frame liquid-metal-frame--soft">
        <div className="flex items-center gap-6 text-sm">
          <SummaryItem label="Total" value={summary.total || extensions.length} color="bg-theme-text-muted" />
          <SummaryItem label="Installed" value={summary.installed ?? 0} color="bg-green-500" />
          <SummaryItem label="Stopped" value={summary.stopped ?? 0} color="bg-red-500" />
          <SummaryItem label="Unhealthy" value={summary.unhealthy ?? 0} color="bg-amber-500" />
          <SummaryItem label="Available" value={summary.not_installed ?? 0} color="bg-theme-accent" />
          <SummaryItem label="Installing" value={summary.installing ?? 0} color="bg-blue-500" />
          <SummaryItem label="Error" value={summary.error ?? 0} color="bg-red-500" />
          <SummaryItem label="Incompatible" value={summary.incompatible ?? 0} color="bg-orange-500" />

          {/* Status legend */}
          <div className="relative ml-auto group/legend" data-tooltip>
            <div className="flex items-center justify-center w-6 h-6 rounded-full border border-theme-border bg-theme-bg/80 text-theme-text-muted cursor-help transition-colors group-hover/legend:text-theme-text group-hover/legend:border-theme-text-muted">
              <Info size={13} />
            </div>
            <div className="pointer-events-none absolute top-[calc(100%+0.5rem)] right-0 z-50 w-96 rounded-lg border border-theme-border bg-theme-card/95 px-4 py-3 opacity-0 shadow-2xl transition-all duration-150 translate-y-1 group-hover/legend:translate-y-0 group-hover/legend:opacity-100">
              <h4 className="text-[10px] font-semibold text-theme-text-secondary uppercase tracking-[0.18em] mb-2.5">Status Legend</h4>
              <div className="grid grid-cols-[5.5rem_1fr] gap-y-2 gap-x-3 items-baseline">
                {Object.entries(STATUS_DESCRIPTIONS).map(([key, desc]) => (
                  <div key={key} className="contents">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded-full uppercase tracking-wider text-center ${STATUS_STYLES[key]}`}>
                      {key.replace(/_/g, ' ')}
                    </span>
                    <span className="text-[11px] leading-4 text-theme-text-secondary">{desc}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Status filter row */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        {STATUS_FILTERS.map(s => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-2.5 py-1 rounded-full text-[10px] font-medium uppercase tracking-[0.12em] border transition-colors ${
              statusFilter === s
                ? 'bg-theme-accent/15 text-theme-accent-light border-theme-accent/25'
                : 'bg-transparent text-theme-text-muted/65 hover:text-theme-text-secondary hover:bg-theme-surface-hover/40 border-theme-border/50'
            }`}
          >
            {STATUS_LABELS[s]}
          </button>
        ))}
      </div>

      {/* Category filter row */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-6">
        <div className="flex flex-wrap gap-1.5">
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-2.5 py-1 rounded-full text-[10px] font-medium uppercase tracking-[0.12em] border transition-colors ${
                category === cat
                  ? 'bg-theme-surface-hover/60 text-theme-text-secondary border-theme-border/60'
                  : 'bg-transparent text-theme-text-muted/55 hover:text-theme-text-secondary hover:bg-theme-surface-hover/40 border-transparent'
              }`}
            >
              {cat === 'all' ? 'All Categories' : cat}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Search extensions..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-theme-bg/60 border border-theme-border/50 text-theme-text placeholder-theme-text-muted/45 rounded-lg px-3 py-1.5 text-xs w-full sm:w-56 outline-none focus:border-theme-accent/30 transition-colors"
        />
      </div>

      {/* Agent offline banner */}
      {catalog?.agent_available === false && (
        <div className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3 text-[11px] text-amber-300/80 flex items-center gap-2.5">
          <span className="shrink-0 text-amber-400">!</span>
          <span>Host agent is offline — install, enable, and disable operations are unavailable. Container logs cannot be fetched.</span>
        </div>
      )}

      {/* Polling-lost banner — 3+ consecutive progress-fetch failures.
          Surfaces when dashboard-api restarts mid-install. Auto-clears on
          the next successful poll. */}
      {pollingLost && (
        <div className="mb-4 rounded-xl border border-amber-500/15 bg-amber-500/[0.04] px-4 py-2 text-[10px] text-amber-400/80 flex items-center gap-2">
          <Loader2 size={10} className="animate-spin shrink-0" />
          <span>Connection to dashboard lost — retrying. Refresh if this persists.</span>
        </div>
      )}

      {/* Card grid */}
      {(() => {
        const enrichedTemplates = templates
          .map(t => ({ ...t, _status: getTemplateStatus(t, extensions) }))
          .filter(t => t._status !== 'applied')
        if (enrichedTemplates.length === 0) return null
        return (
          <div className="mb-4">
            <button onClick={() => setTemplatesOpen(!templatesOpen)} className="flex items-center gap-2 text-sm text-theme-text-muted hover:text-theme-text transition-colors mb-2">
              {templatesOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              Quick Start Templates ({enrichedTemplates.length})
            </button>
            {templatesOpen && <TemplatePicker templates={enrichedTemplates} onApplied={fetchCatalog} />}
          </div>
        )
      })()}

      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-theme-text-muted/50">
          <Package size={40} className="mb-4 opacity-30" />
          <p className="text-sm font-semibold text-theme-text-muted/60">No extensions match</p>
          <p className="text-[10px] uppercase tracking-[0.14em] text-theme-text-muted/40 mt-1.5">Try adjusting your search or filters</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 liquid-metal-sequence-grid liquid-metal-sequence-grid--services">
          {filtered.map(ext => (
            <ExtensionCard
              key={ext.id}
              ext={ext}
              gpuBackend={catalog?.gpu_backend}
              agentAvailable={catalog?.agent_available}
              onDetails={() => setExpanded(ext.id)}
              onConsole={() => setConsoleExt(ext)}
              onAction={requestAction}
              mutating={mutating}
              progressData={progressMap[ext.id]}
            />
          ))}
        </div>
      )}

      {/* Detail modal */}
      {expanded && (
        <DetailModal ext={extensions.find(e => e.id === expanded)} gpuBackend={catalog?.gpu_backend} onClose={() => setExpanded(null)} />
      )}

      {/* Console modal */}
      {consoleExt && (
        <ConsoleModal ext={consoleExt} onClose={() => setConsoleExt(null)} />
      )}

      {/* Confirmation dialog */}
      {confirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setConfirm(null)}>
          <div className="bg-theme-card border border-theme-border rounded-xl p-6 max-w-md mx-4 shadow-2xl" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Confirm action">
            <h3 className="text-base font-semibold text-theme-text mb-2">
              {confirm.action === 'uninstall' ? 'Remove' : confirm.action === 'purge' ? 'Purge Data' : confirm.action.charAt(0).toUpperCase() + confirm.action.slice(1)} Extension
            </h3>
            <p className="text-[11px] text-theme-text-muted/70 mb-5 leading-relaxed">{confirm.message}</p>
            {confirm.action === 'disable' && confirm.ext.dependents?.length > 0 && (
              <DisableDependentWarning dependents={confirm.ext.dependents} />
            )}
            <div className="flex justify-end gap-3">
              <button onClick={() => setConfirm(null)} autoFocus className="px-4 py-2 text-[10px] font-mono uppercase tracking-[0.16em] text-theme-text-muted/65 hover:text-theme-text transition-colors">Cancel</button>
              <button
                onClick={() => handleMutation(confirm.ext.id, confirm.action)}
                className={`px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg transition-colors ${
                  confirm.action === 'uninstall' || confirm.action === 'purge' ? 'bg-red-500/15 text-red-400 hover:bg-red-500/25' :
                  'bg-theme-accent/15 text-theme-accent-light hover:bg-theme-accent/25'
                }`}
              >
                {confirm.action === 'uninstall' ? 'Remove' : confirm.action === 'purge' ? 'Purge' : confirm.action.charAt(0).toUpperCase() + confirm.action.slice(1)}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Dependency auto-enable dialog */}
      {depConfirm && (
        <DependencyConfirmDialog
          ext={depConfirm.ext}
          missingDeps={depConfirm.missingDeps}
          onConfirm={() => handleMutation(depConfirm.ext.id, 'enable', { autoEnableDeps: true })}
          onCancel={() => setDepConfirm(null)}
        />
      )}

      {/* Toast notification */}
      {toast && (
        <div className={`fixed bottom-6 right-6 z-50 rounded-xl border p-4 text-[11px] max-w-sm shadow-2xl ${
          toast.type === 'error' ? 'border-red-500/20 bg-theme-card/95 text-red-300' :
          toast.type === 'info' ? 'border-theme-accent/20 bg-theme-card/95 text-theme-accent-light' :
          'border-green-500/20 bg-theme-card/95 text-green-300'
        }`}>
          <div className="flex items-center justify-between gap-3">
            <span className="leading-relaxed">{toast.text}</span>
            <button onClick={() => setToast(null)} className="text-theme-text-muted/45 hover:text-theme-text-secondary transition-colors">×</button>
          </div>
        </div>
      )}
    </div>
  )
}

function SummaryItem({ label, value, color }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`w-1.5 h-1.5 rounded-full ${color}`} />
      <span className="text-[10px] font-semibold uppercase tracking-[0.13em] text-theme-text-muted/55">{label}</span>
      <span className="text-theme-text font-medium font-mono">{value}</span>
    </div>
  )
}

function StatusBadge({ status, statusStyle, ext, gpuBackend, onConsole }) {
  let tooltip = STATUS_DESCRIPTIONS[status] || ''
  if (status === 'incompatible') {
    tooltip += ` \u2014 requires ${ext.gpu_backends?.join(' or ') || 'specific GPU'}, your system: ${gpuBackend || 'unknown'}`
  }

  const badge = (status === 'installing' || status === 'setting_up') ? (
    <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400 flex items-center gap-1 cursor-help">
      <Loader2 size={8} className="animate-spin" />
      {status === 'setting_up' ? 'setting up' : 'installing'}
    </span>
  ) : status === 'error' ? (
    <span
      className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/20 text-red-300 cursor-pointer"
      onClick={onConsole}
    >
      error
    </span>
  ) : (
    <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider cursor-help ${statusStyle}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )

  return (
    <div className="relative group/status z-[1] hover:z-[60]" data-tooltip>
      {badge}
      {tooltip && (
        <div className="pointer-events-none absolute top-full right-0 z-[60] mt-1.5 w-48 rounded-lg border border-theme-border bg-theme-card/95 px-3 py-2 text-[11px] leading-4 text-theme-text-secondary opacity-0 shadow-2xl transition-all duration-150 translate-y-1 group-hover/status:translate-y-0 group-hover/status:opacity-100">
          {tooltip}
        </div>
      )}
    </div>
  )
}

function LlmSwapBadge({ llm }) {
  if (!llm?.consumes) return null

  const safe = llm.swap_safe === true
  const Icon = safe ? Check : X
  const label = safe ? 'Swap-safe' : 'Not swap-safe'
  const tone = safe
    ? 'border-green-500/20 bg-green-500/10 text-green-300'
    : 'border-red-500/20 bg-red-500/10 text-red-300'

  return (
    <span
      data-testid="llm-swap-badge"
      title={llm.swap_safe_reason || label}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${tone}`}
    >
      <Icon size={10} />
      {label}
    </span>
  )
}

function ExtensionCard({ ext, gpuBackend, agentAvailable, onDetails, onConsole, onAction, mutating, progressData }) {
  const iconName = ext.features?.[0]?.icon
  const Icon = (iconName && ICON_MAP[iconName]) || Package
  const status = ext.status || 'not_installed'
  const statusStyle = STATUS_STYLES[status] || STATUS_STYLES.not_installed
  const isMutating = mutating === ext.id
  const anyMutating = !!mutating
  const agentOffline = agentAvailable === false
  const actionDisabled = anyMutating || agentOffline
  const disabledTitle = agentOffline ? 'Host agent is offline' : anyMutating ? 'Another operation is in progress' : undefined

  const isCore = ext.source === 'core'
  const isUserExt = ext.source === 'user'
  const isError = status === 'error'
  const isStopped = status === 'stopped'
  const isUnhealthy = status === 'unhealthy'
  const isCliInstalled = status === 'cli_installed'
  const isToggleable = isUserExt && (status === 'enabled' || status === 'cli_installed' || status === 'disabled' || status === 'error' || status === 'stopped' || status === 'unhealthy')
  const showRemove = isUserExt && (status === 'disabled' || isError)
  const showInstall = status === 'not_installed' && ext.installable

  return (
    <div className={`bg-theme-card border rounded-xl transition-all liquid-metal-frame liquid-metal-sequence-card flex flex-col ${
      isCore ? 'border-theme-border/60 opacity-70' : 'border-theme-border'
    }`}>
      {/* Card body */}
      <div className="p-4 pb-3 flex-1">
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2.5">
            <div className={`p-1.5 rounded-lg ${
              (status === 'enabled' || status === 'cli_installed') ? 'bg-green-500/10' :
              status === 'stopped' ? 'bg-red-500/10' :
              status === 'unhealthy' ? 'bg-amber-500/10' :
              status === 'incompatible' ? 'bg-orange-500/10' :
              (status === 'installing' || status === 'setting_up') ? 'bg-blue-500/10' :
              status === 'error' ? 'bg-red-500/10' :
              'bg-theme-bg border border-theme-border/30'
            }`}>
              <Icon size={16} className={
                (status === 'enabled' || status === 'cli_installed') ? 'text-green-400' :
                status === 'stopped' ? 'text-red-400' :
                status === 'unhealthy' ? 'text-amber-400' :
                status === 'incompatible' ? 'text-orange-400' :
                (status === 'installing' || status === 'setting_up') ? 'text-blue-400' :
                status === 'error' ? 'text-red-400' :
                'text-theme-text-muted'
              } />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-theme-text leading-tight">{ext.name}</h3>
              {ext.features?.[0]?.category && (
                <span className="text-[9px] text-theme-text-secondary/70 uppercase tracking-[0.18em]">{ext.features[0].category}</span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <LlmSwapBadge llm={ext.llm} />
            {isCore ? (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/15 uppercase tracking-wider cursor-help"
                title="Built-in service — managed by ODS"
              >
                core
              </span>
            ) : (
              <StatusBadge status={status} statusStyle={statusStyle} ext={ext} gpuBackend={gpuBackend} onConsole={onConsole} />
            )}
            {isToggleable && (
              <button
                disabled={actionDisabled}
                title={disabledTitle}
                onClick={() => onAction(ext, status === 'disabled' ? 'enable' : 'disable')}
                className={`relative inline-flex h-[18px] w-[32px] shrink-0 rounded-full transition-colors disabled:opacity-50 ${
                  status === 'error' ? 'bg-red-500' :
                  status === 'stopped' ? 'bg-amber-500' :
                  status === 'unhealthy' ? 'bg-amber-500' :
                  (status === 'enabled' || isCliInstalled) ? 'bg-green-500' : 'bg-theme-border'
                }`}
              >
                {isMutating ? (
                  <Loader2 size={8} className="animate-spin absolute top-[3px] left-[10px] text-white" />
                ) : (
                  <span className={`pointer-events-none inline-block h-[14px] w-[14px] rounded-full bg-white shadow-sm transform transition-transform mt-[2px] ${
                    status === 'disabled' ? 'translate-x-[2px]' : 'translate-x-[16px]'
                  }`} />
                )}
              </button>
            )}
          </div>
        </div>
        <p className="text-[11px] text-theme-text-secondary/85 line-clamp-2 leading-relaxed">{ext.description || 'No description available.'}</p>
      </div>

      {/* Progress indicator — shows during active install/setup, survives page refresh */}
      {(progressData || ext.status === 'installing' || ext.status === 'setting_up') && (
        <div className="px-4 py-2 border-t border-theme-border/40 text-[10px] text-blue-400/80 flex items-center gap-2">
          <Loader2 size={12} className="animate-spin" />
          <span>{progressData?.phase_label || (ext.status === 'setting_up' ? 'Running setup...' : 'Installing...')}</span>
        </div>
      )}
      {/* Error message — expandable when long or multiline so docker-compose
          stderr isn't cut off mid-actionable-line. */}
      {ext.status === 'error' && progressData?.error && (() => {
        const errorText = progressData.error
        const firstLine = errorText.split('\n')[0]
        const isMultiline = errorText.length > firstLine.length
        const isLongLine = firstLine.length > 120
        const needsExpand = isMultiline || isLongLine
        if (!needsExpand) {
          return (
            <div className="px-4 py-2 border-t border-red-500/15 text-[10px] text-red-300/80 leading-relaxed">
              {errorText}
            </div>
          )
        }
        const summaryText = isLongLine
          ? firstLine.slice(0, 120) + '...'
          : firstLine + (isMultiline ? '...' : '')
        return (
          <details className="group px-4 py-2 border-t border-red-500/15 text-[10px] text-red-300/80 leading-relaxed">
            <summary className="cursor-pointer flex items-start gap-1 list-none [&::-webkit-details-marker]:hidden hover:text-red-300">
              <ChevronDown size={10} className="mt-0.5 shrink-0 transition-transform group-open:rotate-180" />
              <span className="flex-1 break-words">{summaryText}</span>
            </summary>
            <pre className="whitespace-pre-wrap text-[10px] text-red-300/80 mt-2 font-mono break-words">{errorText}</pre>
          </details>
        )
      })()}

      {/* Card footer */}
      <div className="border-t border-theme-border/40 px-4 py-2.5 flex items-center justify-between bg-theme-bg/30">
        <div className="flex gap-1.5">
          {showInstall && (
            <button
              disabled={actionDisabled}
              title={disabledTitle}
              onClick={() => onAction(ext, 'install')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg bg-theme-accent text-white hover:bg-theme-accent-hover transition-colors disabled:opacity-50 shadow-sm shadow-theme-accent/20"
            >
              {isMutating ? <Loader2 size={12} className="animate-spin" /> : <><Download size={12} /> Install</>}
            </button>
          )}
          {isUserExt && isStopped && (
            <button
              disabled={actionDisabled}
              title={disabledTitle}
              onClick={() => onAction(ext, 'enable')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg bg-green-500/15 text-green-400 hover:bg-green-500/25 transition-colors disabled:opacity-50"
            >
              {isMutating ? <Loader2 size={12} className="animate-spin" /> : 'Start'}
            </button>
          )}
          {isUserExt && isUnhealthy && (
            <button
              onClick={onConsole}
              disabled={agentOffline}
              title={agentOffline ? 'Host agent is offline' : 'View container logs'}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors disabled:opacity-50"
            >
              <Terminal size={12} /> Check Logs
            </button>
          )}
          {isError && (
            <button
              disabled={actionDisabled}
              title={disabledTitle}
              onClick={() => onAction(ext, 'enable')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg bg-blue-500/15 text-blue-400 hover:bg-blue-500/25 transition-colors disabled:opacity-50"
            >
              {isMutating ? <Loader2 size={12} className="animate-spin" /> : <><RefreshCw size={12} /> Retry</>}
            </button>
          )}
          {showRemove && (
            <button
              disabled={actionDisabled}
              title={disabledTitle}
              onClick={() => onAction(ext, 'uninstall')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg bg-transparent text-red-400/80 hover:bg-red-500/15 hover:text-red-300 transition-colors disabled:opacity-50"
            >
              {isMutating ? <Loader2 size={12} className="animate-spin" /> : <><Trash2 size={12} /> Remove</>}
            </button>
          )}
          {showRemove && ext.has_data && (
            <button
              disabled={actionDisabled}
              title={disabledTitle || 'Permanently delete service data'}
              onClick={() => onAction(ext, 'purge')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] rounded-lg bg-transparent text-amber-400/80 hover:bg-amber-500/15 hover:text-amber-300 transition-colors disabled:opacity-50"
            >
              {isMutating ? <Loader2 size={12} className="animate-spin" /> : <><Database size={12} /> Purge Data</>}
            </button>
          )}
          {isUserExt && (status === 'enabled' || isCliInstalled) && (
            <span className="text-[9px] uppercase tracking-[0.14em] text-theme-text-muted/45">Disable to remove</span>
          )}
          {!showInstall && !showRemove && !isToggleable && (
            <div className="flex items-center gap-1" title={status === 'incompatible' && gpuBackend ? `Your system: ${gpuBackend}` : undefined}>
              {status === 'incompatible' && <span className="text-[9px] uppercase tracking-[0.14em] text-theme-text-muted/45 mr-0.5">Requires:</span>}
              {ext.gpu_backends?.slice(0, 3).map(gpu => (
                <span key={gpu} className="text-[9px] px-1.5 py-0.5 rounded-full border border-theme-border/50 bg-theme-surface-hover/30 text-theme-text-muted/65 font-mono uppercase tracking-[0.1em]">{gpu}</span>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <DependencyBadges dependsOn={ext.depends_on} dependencyStatus={ext.dependency_status} />
          {status === 'enabled' && (ext.external_port_default || ext.port) && (ext.external_port_default || ext.port) !== 0 ? (
            HEADLESS_EXTENSIONS.has(ext.id) ? (
              <span className="px-2 py-1 text-[9px] font-mono uppercase tracking-[0.12em] text-theme-text-muted/45">
                API service
              </span>
            ) : (
              <a
                href={`http://${window.location.hostname}:${ext.external_port_default || ext.port}`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={e => e.stopPropagation()}
                className="flex items-center gap-1 px-2 py-1.5 text-[10px] font-mono text-theme-text-secondary hover:text-theme-text hover:bg-theme-surface-hover/40 rounded-lg transition-colors"
                title={`Open on port ${ext.external_port_default || ext.port}`}
              >
                <ExternalLink size={11} />
                :{ext.external_port_default || ext.port}
              </a>
            )
          ) : null}
          {(isUserExt || isCore) && status !== 'not_installed' && (
            <button
              onClick={onConsole}
              disabled={agentOffline}
              className={`flex items-center gap-1.5 px-2 py-1.5 text-[10px] rounded-lg transition-colors ${
                agentOffline ? 'text-theme-text-muted/40 cursor-not-allowed' :
                isError ? 'text-red-400 hover:text-red-300 hover:bg-red-500/10' :
                (status === 'installing' || isStopped || isUnhealthy) ? 'text-amber-400/80 hover:text-amber-300 hover:bg-amber-500/10' :
                'text-theme-text-secondary hover:text-theme-text hover:bg-theme-surface-hover/40'
              }`}
              title={agentOffline ? 'Agent offline' : 'View logs'}
            >
              <Terminal size={14} />
              <span>Logs</span>
            </button>
          )}
          <button
            onClick={onDetails}
            className="flex items-center gap-1 px-2 py-1.5 text-[10px] text-theme-text-secondary hover:text-theme-text hover:bg-theme-surface-hover/40 rounded-lg transition-colors"
          >
            <Info size={11} />
          </button>
        </div>
      </div>
    </div>
  )
}

function DetailModal({ ext, gpuBackend, onClose }) {
  useEffect(() => {
    if (!ext) return
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [ext, onClose])

  if (!ext) return null

  const iconName = ext.features?.[0]?.icon
  const Icon = (iconName && ICON_MAP[iconName]) || Package
  const envVars = ext.env_vars || []
  const deps = ext.depends_on || []
  const features = ext.features || []
  const statusStyle = STATUS_STYLES[ext.status] || STATUS_STYLES.not_installed
  const isIncompatible = ext.status === 'incompatible'

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-theme-card border border-theme-border rounded-xl w-full max-w-lg max-h-[80vh] overflow-y-auto mx-4"
        onClick={e => e.stopPropagation()}
        role="dialog" aria-modal="true" aria-label={ext.name}
      >
        {/* Header */}
        <div className="sticky top-0 bg-theme-card border-b border-theme-border p-4 flex items-center justify-between rounded-t-xl">
          <div className="flex items-center gap-3">
            <Icon size={22} className="text-theme-text-muted" />
            <div>
              <h3 className="text-lg font-semibold text-theme-text">{ext.name}</h3>
              <span
                className={`text-xs px-2 py-0.5 rounded-full ${statusStyle}`}
                title={isIncompatible ? `Requires ${ext.gpu_backends?.join(' or ') || 'specific GPU'} — your system: ${gpuBackend || 'unknown'}` : ext.source === 'core' ? 'Built-in service — managed by ODS' : undefined}
              >
                {(ext.status || 'not_installed').replace('_', ' ')}
              </span>
              <div className="mt-1">
                <LlmSwapBadge llm={ext.llm} />
              </div>
            </div>
          </div>
          <button onClick={onClose} autoFocus className="text-theme-text-muted hover:text-theme-text-secondary transition-colors p-1">
            <X size={18} />
          </button>
        </div>

        <div className="p-4 space-y-4">
          {/* Description */}
          <p className="text-sm text-theme-text-muted">{ext.description || 'No description available.'}</p>

          {/* Info grid */}
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="bg-theme-card/50 rounded-lg p-3">
              <span className="text-theme-text-muted text-xs block mb-1">Port</span>
              <span className="text-theme-text font-mono">{ext.external_port_default || ext.port || '—'}</span>
            </div>
            <div className="bg-theme-card/50 rounded-lg p-3">
              <span className="text-theme-text-muted text-xs block mb-1">GPU</span>
              <span className="text-theme-text">{ext.gpu_backends?.join(', ') || 'none'}</span>
              {isIncompatible && gpuBackend && (
                <span className="text-orange-400 text-[10px] block mt-1">Your system: {gpuBackend}</span>
              )}
            </div>
            <div className="bg-theme-card/50 rounded-lg p-3">
              <span className="text-theme-text-muted text-xs block mb-1">Category</span>
              <span className="text-theme-text">{ext.category || '—'}</span>
            </div>
            <div className="bg-theme-card/50 rounded-lg p-3">
              <span className="text-theme-text-muted text-xs block mb-1">Health</span>
              <span className="text-theme-text font-mono text-xs">{ext.health_endpoint || '—'}</span>
            </div>
          </div>

          {/* Dependencies */}
          {deps.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-2">Dependencies</h4>
              <div className="flex flex-wrap gap-2">
                {deps.map(dep => (
                  <span key={dep} className="bg-theme-card text-theme-text-muted rounded px-2 py-1 text-xs">{dep}</span>
                ))}
              </div>
            </div>
          )}

          {/* Environment variables */}
          {envVars.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-2">Environment Variables</h4>
              <div className="bg-theme-card/50 rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-theme-border">
                      <th className="text-left px-3 py-2 text-theme-text-muted font-medium text-xs">Key</th>
                      <th className="text-left px-3 py-2 text-theme-text-muted font-medium text-xs">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {envVars.map(v => (
                      <tr key={v.key || v.name} className="border-b border-theme-border/50 last:border-0">
                        <td className="px-3 py-2 text-theme-accent-light font-mono text-xs">{v.key || v.name}</td>
                        <td className="px-3 py-2 text-theme-text-muted text-xs">{v.description || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Features */}
          {features.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-2">Features</h4>
              <div className="space-y-1">
                {features.map(feat => (
                  <div key={feat.name} className="flex items-center gap-2 text-sm">
                    <span className="w-1.5 h-1.5 rounded-full bg-theme-accent" />
                    <span className="text-theme-text-secondary">{feat.name}</span>
                    {feat.category && <span className="text-xs text-theme-text-muted">({feat.category})</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Login / Credentials */}
          {envVars.some(v => /password|secret|token|key/i.test(v.key || '')) && (
            <div>
              <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-2">Login Credentials</h4>
              <p className="text-xs text-theme-text-muted mb-2">Run this in your terminal to see login info:</p>
              <CopyableCommand command={
                `docker exec ods-${ext.id} env | grep -iE "${envVars.filter(v => /username|password|secret|token|key|user|email/i.test(v.key || '')).map(v => v.key).join('|')}"`
              } />
              <p className="text-xs text-theme-text-muted mt-1.5">Or check your .env file directly:</p>
              <CopyableCommand command={
                `grep -E "${envVars.filter(v => /username|password|secret|token|key|user|email/i.test(v.key || '')).map(v => v.key).join('|')}" .env`
              } />
            </div>
          )}

          {/* CLI Commands */}
          <div>
            <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-2">CLI Commands</h4>
            <div className="space-y-1">
              <CopyableCommand command={`ods enable ${ext.id}`} />
              <CopyableCommand command={`ods disable ${ext.id}`} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function ConsoleModal({ ext, onClose }) {
  const [logs, setLogs] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [disconnected, setDisconnected] = useState(false)
  const [atBottom, setAtBottom] = useState(true)
  const [installInfo, setInstallInfo] = useState(null)
  const logRef = useRef(null)
  const isNearBottom = useRef(true)

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  // Fetch install progress info
  useEffect(() => {
    let active = true
    const fetchProgress = async () => {
      try {
        const res = await fetchJson(`/api/extensions/${ext.id}/progress`)
        if (res.ok && active) {
          const data = await res.json()
          if (data.status !== 'idle') setInstallInfo(data)
        }
      } catch { /* ignore */ }
    }
    fetchProgress()
    const interval = setInterval(fetchProgress, 5000)
    return () => { active = false; clearInterval(interval) }
  }, [ext.id])

  useEffect(() => {
    let active = true
    // Tracker drives the disconnected banner after 3 consecutive failures.
    // We also keep the raw failure count locally for the exponential
    // backoff calculation (trackers don't expose internal state on success).
    let failCount = 0
    const tracker = createRecoveryTracker({
      threshold: 3,
      onThresholdReached: () => setDisconnected(true),
      // setDisconnected(false) is already done in the success branch below,
      // so no onRecovered callback is needed here.
    })

    const poll = async () => {
      if (!active) return
      try {
        const res = await fetch(`/api/extensions/${ext.id}/logs`, {
          method: 'POST',
          signal: AbortSignal.timeout(8000),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          throw new Error(err.detail || 'Failed to fetch logs')
        }
        const data = await res.json()
        setLogs(data.logs || 'No logs available.')
        setError(null)
        setDisconnected(false)
        tracker.recordSuccess()
        failCount = 0
      } catch (err) {
        failCount = tracker.recordFailure()
        setError(err.message)
      } finally {
        setLoading(false)
      }
      if (active) {
        const delay = failCount > 0 ? Math.min(2000 * Math.pow(2, failCount - 1), 30000) : 2000
        setTimeout(poll, delay)
      }
    }
    poll()
    return () => { active = false }
  }, [ext.id])

  useEffect(() => {
    if (logRef.current && isNearBottom.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs])

  const handleScroll = () => {
    if (logRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = logRef.current
      const near = scrollHeight - scrollTop - clientHeight < 50
      isNearBottom.current = near
      setAtBottom(near)
    }
  }

  const scrollToBottom = () => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
      isNearBottom.current = true
      setAtBottom(true)
    }
  }

  const fetchLogsOnce = async () => {
    try {
      const res = await fetch(`/api/extensions/${ext.id}/logs`, {
        method: 'POST',
        signal: AbortSignal.timeout(8000),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to fetch logs')
      }
      const data = await res.json()
      setLogs(data.logs || 'No logs available.')
      setError(null)
      setDisconnected(false)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-theme-bg border border-theme-border rounded-xl w-full max-w-3xl h-[70vh] flex flex-col mx-4"
        onClick={e => e.stopPropagation()}
        role="dialog" aria-modal="true" aria-label={`${ext.name} logs`}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-theme-border">
          <div className="flex items-center gap-2">
            <Terminal size={16} className={disconnected ? 'text-red-400' : 'text-green-400'} />
            <span className="text-sm font-medium text-theme-text">{ext.name}</span>
            <span className="text-xs text-theme-text-muted">logs</span>
            {disconnected ? (
              <span className="w-1.5 h-1.5 rounded-full bg-red-500" title="Disconnected" />
            ) : (
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" title="Live" />
            )}
          </div>
          <button onClick={onClose} autoFocus className="text-theme-text-muted hover:text-theme-text-secondary transition-colors p-1">
            <X size={16} />
          </button>
        </div>
        {installInfo && (
          <div className={`px-4 py-2 border-b text-xs flex items-center gap-2 ${
            installInfo.status === 'error' ? 'border-red-500/30 bg-red-500/10 text-red-300' :
            installInfo.status === 'started' ? 'border-green-500/30 bg-green-500/10 text-green-300' :
            'border-blue-500/30 bg-blue-500/10 text-blue-300'
          }`}>
            {installInfo.status !== 'error' && installInfo.status !== 'started' && (
              <Loader2 size={12} className="animate-spin" />
            )}
            <span className="font-medium">{installInfo.phase_label || installInfo.status}</span>
            {installInfo.error && (
              <span className="ml-2 text-red-300">— {installInfo.error}</span>
            )}
            {installInfo.started_at && (
              <span className="ml-auto text-theme-text-muted">
                {new Date(installInfo.started_at).toLocaleTimeString()}
              </span>
            )}
          </div>
        )}
        <div className="relative flex-1">
          <div
            ref={el => { logRef.current = el }}
            onScroll={handleScroll}
            className="absolute inset-0 overflow-y-auto p-4 font-mono text-xs leading-relaxed text-theme-text-secondary whitespace-pre-wrap break-all"
          >
            {loading && !logs ? (
              <div className="flex items-center gap-2 text-theme-text-muted">
                <Loader2 size={14} className="animate-spin" /> Loading logs...
              </div>
            ) : (
              <>
                {logs}
                {error && logs && (
                  <div className="mt-2 text-red-400 border-t border-red-500/20 pt-2">
                    {disconnected ? 'Connection lost' : 'Fetch error'}: {error}
                  </div>
                )}
              </>
            )}
            {error && !logs && (
              <div className="text-red-400">{error}</div>
            )}
          </div>
          {!atBottom && (
            <button
              onClick={scrollToBottom}
              className="absolute bottom-2 right-4 bg-theme-card border border-theme-border text-theme-text-muted hover:text-theme-text rounded-full px-3 py-1 text-xs shadow-lg transition-colors"
            >
              ↓ Jump to bottom
            </button>
          )}
        </div>
        <div className="border-t border-theme-border px-4 py-2 flex items-center justify-between">
          <span className={`text-[10px] ${disconnected ? 'text-red-400' : 'text-theme-text-muted'}`}>
            {disconnected ? 'Reconnecting...' : 'Auto-refreshing every 2s'}
          </span>
          <button onClick={fetchLogsOnce} className="text-xs text-theme-text-muted hover:text-theme-text-secondary transition-colors" title="Refresh now">
            <RefreshCw size={12} />
          </button>
        </div>
      </div>
    </div>
  )
}

function CopyableCommand({ command }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard?.writeText(command)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
      .catch(() => {})
  }

  return (
    <div className="group flex items-center justify-between bg-theme-card rounded px-3 py-1.5 font-mono text-sm text-theme-text-secondary">
      <span className="truncate mr-2">{command}</span>
      <button
        onClick={handleCopy}
        className="shrink-0 text-theme-text-muted hover:text-theme-text-secondary transition-colors"
        title="Copy to clipboard"
      >
        {copied ? <Check size={13} className="text-green-400" /> : <Copy size={13} />}
      </button>
    </div>
  )
}
