import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownToLine,
  Box,
  CheckCircle2,
  Cloud,
  Cpu,
  ExternalLink,
  FileArchive,
  Gauge,
  HardDrive,
  Heart,
  KeyRound,
  Loader2,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react'
import HelpLink from '../HelpLink'

const SEARCH_DELAY_MS = 350
const SEARCH_TIMEOUT_MS = 30000
const IMPORT_TIMEOUT_MS = 45000
// The check reads the repository and one file's header from the Hub, then
// this machine's storage; on a slow link that outlasts a search (Mac mini,
// 2026-10-09). It never blocks an import.
const PREFLIGHT_TIMEOUT_MS = 90000

async function boundedJsonRequest(url, options = {}, timeout = SEARCH_TIMEOUT_MS,
  timeoutMessage = 'The request timed out. Check download status before retrying.') {
  const controller = new AbortController()
  let timer
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(url, { ...options, signal: controller.signal })
        const body = await responseJson(response)
        if (!response.ok) {
          const error = new Error(errorMessage(body, 'Could not confirm the model operation.'))
          error.rejected = (response.status >= 400 && response.status < 500) ||
            response.headers?.get('X-ODS-Import-Started') === 'false'
          error.detail = body?.detail
          throw error
        }
        return body
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error(timeoutMessage))
          controller.abort()
        }, timeout)
      }),
    ])
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
}

export default function HuggingFaceModelBrowser({ gpu, downloadBusy, onImportStarted }) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('downloads')
  const [results, setResults] = useState([])
  const [authenticated, setAuthenticated] = useState(false)
  const [stale, setStale] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedRepo, setSelectedRepo] = useState(null)
  const [details, setDetails] = useState(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState(null)
  const [importingArtifact, setImportingArtifact] = useState(null)
  const [pendingImport, setPendingImport] = useState(null)
  const [importNotice, setImportNotice] = useState('')
  const [checkingImport, setCheckingImport] = useState(false)
  const importLock = useRef(false)
  const checkImportLock = useRef(false)
  const [searchAttempt, setSearchAttempt] = useState(0)
  const detailsRequestRef = useRef(0)
  const [preflight, setPreflight] = useState(null)
  const [preflightLoading, setPreflightLoading] = useState(false)
  const [preflightError, setPreflightError] = useState(null)
  const [overrideOffer, setOverrideOffer] = useState(null)

  // Facts about the repository's GGUFs before any download: whether this
  // machine's llama.cpp can load it, whether it is a chat model, fit and disk.
  const loadPreflight = async (modelId, requestId) => {
    setPreflight(null)
    setPreflightError(null)
    setPreflightLoading(true)
    try {
      const body = await boundedJsonRequest(`/api/models/huggingface/preflight/${encodeURI(modelId)}`, {},
        PREFLIGHT_TIMEOUT_MS, 'the check took longer than 90 seconds')
      if (detailsRequestRef.current !== requestId) return
      if (body?.id !== modelId || !body.artifacts || typeof body.artifacts !== 'object'
          || Array.isArray(body.artifacts) || typeof body.modelKind !== 'string') {
        throw new Error('the check returned an unexpected answer')
      }
      setPreflight(body)
    } catch (requestError) {
      if (detailsRequestRef.current === requestId) setPreflightError(requestError.message)
    } finally {
      if (detailsRequestRef.current === requestId) setPreflightLoading(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    let deadline
    const timeout = setTimeout(async () => {
      // Include body consumption in the deadline. Cleanup aborts obsolete
      // queries; a current stalled request must also release the Retry action.
      deadline = setTimeout(() => {
        controller.abort()
        setError('Hugging Face search timed out. Retry search.')
        setLoading(false)
      }, SEARCH_TIMEOUT_MS)
      try {
        const params = new URLSearchParams({ q: query.trim(), sort, limit: '20' })
        const response = await fetch(`/api/models/huggingface/search?${params}`, { signal: controller.signal })
        const body = await responseJson(response)
        if (controller.signal.aborted) return
        if (!response.ok) throw new Error(errorMessage(body, 'Hugging Face search failed'))
        setResults(Array.isArray(body.models) ? body.models : [])
        setAuthenticated(Boolean(body.authenticated))
        setStale(Boolean(body.stale))
      } catch (requestError) {
        if (!controller.signal.aborted && requestError?.name !== 'AbortError') setError(requestError.message)
      } finally {
        clearTimeout(deadline)
        if (!controller.signal.aborted) setLoading(false)
      }
    }, SEARCH_DELAY_MS)
    return () => {
      clearTimeout(timeout)
      clearTimeout(deadline)
      controller.abort()
    }
  }, [query, sort, searchAttempt])

  const openRepository = async (model) => {
    const requestId = detailsRequestRef.current + 1
    detailsRequestRef.current = requestId
    setSelectedRepo(model)
    setDetails(null)
    setDetailsError(null)
    setPreflight(null)
    setPreflightError(null)
    setPreflightLoading(false)
    setDetailsLoading(true)
    try {
      const body = await boundedJsonRequest(`/api/models/huggingface/repositories/${encodeURI(model.id)}`)
      if (detailsRequestRef.current !== requestId) return
      if (body?.id !== model.id || !Array.isArray(body.artifacts)) {
        throw new Error('Could not read repository metadata. Retry details.')
      }
      setDetails(body)
      if (body.runtimeCompatible !== false && body.artifacts.length > 0) {
        loadPreflight(model.id, requestId)
      }
    } catch (requestError) {
      if (detailsRequestRef.current === requestId) setDetailsError(requestError.message)
    } finally {
      if (detailsRequestRef.current === requestId) setDetailsLoading(false)
    }
  }

  const closeRepository = () => {
    detailsRequestRef.current += 1
    setSelectedRepo(null)
    setDetails(null)
    setDetailsError(null)
    setPreflight(null)
    setPreflightError(null)
    setPreflightLoading(false)
  }

  const importArtifact = async (artifact, { allowUnsupportedRuntime = false } = {}) => {
    if (!details?.id || downloadBusy || pendingImport || importLock.current) return
    importLock.current = true
    const request = { repoId: details.id, artifactId: artifact.id }
    if (allowUnsupportedRuntime) request.allowUnsupportedRuntime = true
    setOverrideOffer(null)
    setPendingImport({ ...request, startedAt: Date.now() })
    setImportNotice('Starting the import. You can close this dialog; the download will continue on the host.')
    setImportingArtifact(artifact.id)
    setDetailsError(null)
    try {
      const body = await boundedJsonRequest('/api/models/huggingface/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      }, IMPORT_TIMEOUT_MS)
      if (typeof body?.modelId !== 'string' || !body.modelId.trim()) {
        throw new Error('The import acknowledgement is incomplete.')
      }
      setPendingImport(null)
      setImportNotice('')
      Promise.resolve(onImportStarted?.(body)).catch(() => setImportNotice('Import accepted. Refresh Models to check download progress.'))
      setSelectedRepo(current => current?.id === request.repoId ? null : current)
      setDetails(current => current?.id === request.repoId ? null : current)
    } catch (requestError) {
      if (requestError.rejected) setPendingImport(null)
      setImportNotice(requestError.rejected ? requestError.message : `${requestError.message} The host may still be processing this import. Check its status; no second download has been requested.`)
      const detail = requestError.detail
      setOverrideOffer(
        requestError.rejected && !allowUnsupportedRuntime && detail?.overridable === true
          && detail?.code === 'runtime_architecture_unsupported' ? artifact : null,
      )
    } finally {
      setImportingArtifact(null)
      importLock.current = false
    }
  }

  const reconcileImport = async () => {
    if (!pendingImport || importLock.current || checkImportLock.current) return
    checkImportLock.current = true
    setCheckingImport(true)
    try {
      const [repository, catalog, progress] = await Promise.all([
        boundedJsonRequest(`/api/models/huggingface/repositories/${encodeURI(pendingImport.repoId)}`),
        boundedJsonRequest('/api/models'),
        boundedJsonRequest('/api/models/download-status'),
      ])
      const artifact = repository.id === pendingImport.repoId && Array.isArray(repository.artifacts) && repository.artifacts.find(item => item.id === pendingImport.artifactId)
      const model = artifact?.importedModelId && Array.isArray(catalog.models) && catalog.models.find(item => item.id === artifact.importedModelId)
      const observed = progress.status === 'idle' && progress.lastTerminalStatus
        ? progress.lastTerminalStatus : progress
      const label = typeof observed.model === 'string' ? observed.model : ''
      const matches = model && [model.id, model.gguf].some(value => typeof value === 'string' && value && (label === value || label.startsWith(value + ' (')))
      const sampledAt = Date.parse(observed.updatedAt || '')
      if (!matches || !Number.isFinite(sampledAt) || sampledAt < pendingImport.startedAt ||
          !['downloading', 'verifying', 'complete', 'failed', 'error', 'cancelled', 'canceled'].includes(observed.status)) {
        throw new Error('This import is not yet confirmed. Check status again before requesting another download.')
      }
      // A readback of this exact artifact resolves the uncertain POST. Never
      // replay it: accepted downloads continue through the shared progress UI.
      Promise.resolve(onImportStarted?.({ modelId: model.id, status: observed.status }))
        .catch(() => setImportNotice('Import found. Refresh Models to check download progress.'))
      setPendingImport(null)
      setImportNotice('')
      setSelectedRepo(null)
      setDetails(null)
    } catch (error) {
      setImportNotice(error.message)
    } finally {
      checkImportLock.current = false
      setCheckingImport(false)
    }
  }

  const importStatus = importNotice && <div role="status" className="rounded-lg border border-theme-border p-3 text-sm">
        <p>{importNotice}</p>
        {pendingImport && <button type="button" onClick={reconcileImport} disabled={Boolean(importingArtifact || checkingImport)} className="mt-2 rounded border border-theme-border px-3 py-1 disabled:opacity-50">
          {checkingImport ? 'Checking download…' : 'Check download status'}
        </button>}
        {overrideOffer && !pendingImport && (
          <button type="button" onClick={() => importArtifact(overrideOffer, { allowUnsupportedRuntime: true })} disabled={Boolean(importingArtifact)} className="mt-2 mr-3 rounded border border-theme-border px-3 py-1 disabled:opacity-50">
            Import anyway
          </button>
        )}
        {!pendingImport && <HelpLink className="mt-2 text-xs text-theme-text-muted" />}
      </div>

  return (
    <div className="space-y-4">
      {!selectedRepo && importStatus}
      <section className="grid gap-3 border-b border-white/[0.06] pb-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-theme-border bg-theme-text-secondary/8">
            <img src="/huggingface-logo.svg" alt="" className="h-7 w-7 object-contain" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-theme-text">Hugging Face Hub</h2>
              <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${authenticated ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-300' : 'border-white/[0.08] bg-white/[0.04] text-theme-text-muted'}`}>
                {authenticated ? <KeyRound size={10} /> : <ShieldCheck size={10} />}
                {authenticated ? 'Authenticated' : 'Public access'}
              </span>
              {stale && (
                <span className="inline-flex items-center gap-1 rounded border border-theme-border bg-theme-text-secondary/10 px-1.5 py-0.5 text-[10px] font-semibold text-theme-text-secondary">
                  Cached snapshot
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-theme-text-muted">
              Community GGUF discovery. ODS verifies the exact file size and SHA-256 before installation.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-theme-text-muted" aria-live="polite">
          <span className="inline-flex min-w-[112px] items-center justify-center gap-1.5 rounded-md border border-white/[0.06] bg-black/20 px-2.5 py-1.5">
            {loading && <Loader2 size={11} className="animate-spin text-theme-text-secondary" />}
            {loading ? 'Searching...' : `${results.length} repositories`}
          </span>
          <span className="rounded-md border border-white/[0.06] bg-black/20 px-2.5 py-1.5">GGUF only</span>
        </div>
      </section>

      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px]">
        <label className="relative block">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-theme-text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search repositories, authors, or model families..."
            className="h-10 w-full rounded-lg border border-white/[0.08] bg-black/25 pl-10 pr-10 text-sm text-theme-text outline-none transition-colors placeholder:text-theme-text-muted/60 focus:border-theme-border"
            aria-busy={loading}
          />
          {loading && <Loader2 size={15} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-theme-text-secondary" />}
        </label>
        <select
          value={sort}
          onChange={(event) => setSort(event.target.value)}
          className="h-10 rounded-lg border border-white/[0.08] bg-[#0b0b12] px-3 text-xs text-theme-text-secondary outline-none focus:border-theme-border"
          aria-label="Sort Hugging Face models"
        >
          <option value="downloads">Most downloaded</option>
          <option value="likes">Most liked</option>
          <option value="lastModified">Recently updated</option>
        </select>
      </div>

      {error && (
        <div role="alert" className="flex flex-col gap-3 rounded-lg border border-red-400/25 bg-red-500/10 px-4 py-3 text-sm text-red-300 sm:flex-row sm:items-center sm:justify-between">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setSearchAttempt(attempt => attempt + 1)}
            disabled={loading}
            className="inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-md border border-red-300/25 bg-red-400/10 px-3 text-xs font-semibold text-red-100 transition-colors hover:bg-red-400/20 disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Retry search
          </button>
        </div>
      )}

      <section className="relative overflow-hidden rounded-lg border border-white/[0.08] bg-black/15" aria-busy={loading}>
        {loading && (
          <div className="absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden bg-theme-text-secondary/10">
            <div className="h-full w-full animate-pulse bg-gradient-to-r from-transparent via-theme-text-secondary to-transparent" />
          </div>
        )}
        <div className="hidden grid-cols-[minmax(280px,1.5fr)_120px_100px_110px_140px] gap-4 border-b border-white/[0.06] px-5 py-3 text-[9px] font-semibold uppercase tracking-[0.16em] text-theme-text-muted/60 lg:grid">
          <span>Repository</span>
          <span>Activity</span>
          <span>License</span>
          <span>Artifacts</span>
          <span>Action</span>
        </div>
        <div className="divide-y divide-white/[0.05]">
          {loading && results.length === 0 && <RepositorySkeleton />}
          {!loading && !error && results.length === 0 && (
            <div className="px-5 py-16 text-center">
              <Cloud size={28} className="mx-auto text-theme-text-muted/45" />
              <p className="mt-3 text-sm font-medium text-theme-text-secondary">No GGUF repositories found</p>
              <p className="mt-1 text-xs text-theme-text-muted">Try a model family such as Qwen, Gemma, Llama, or Mistral.</p>
            </div>
          )}
          {results.map(model => (
            <RepositoryRow key={model.id} model={model} onInspect={() => openRepository(model)} />
          ))}
        </div>
      </section>

      {selectedRepo && (
        <ArtifactDialog
          key={selectedRepo.id}
          model={selectedRepo}
          details={details}
          loading={detailsLoading}
          error={detailsError}
          preflight={preflight}
          preflightLoading={preflightLoading}
          preflightError={preflightError}
          gpu={gpu}
          downloadBusy={downloadBusy || Boolean(pendingImport)}
          importingArtifact={importingArtifact}
          importStatus={importStatus}
          onClose={closeRepository}
          onImport={importArtifact}
          onRetry={() => openRepository(selectedRepo)}
        />
      )}
    </div>
  )
}

function RepositoryRow({ model, onInspect }) {
  const [avatarFailed, setAvatarFailed] = useState(false)
  const fallbackStyle = authorFallbackStyle(model.author)
  return (
    <div className="hf-repository-row grid grid-cols-2 gap-3 px-4 py-4 transition-colors hover:bg-white/[0.025] sm:grid-cols-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(280px,1.5fr)_120px_100px_110px_140px] lg:items-center lg:gap-4 lg:px-5 lg:py-3.5">
      <div className="col-span-2 flex min-w-0 items-start gap-3 sm:col-span-1">
        <div className="relative flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg border text-xs font-bold" style={fallbackStyle}>
          <span>{model.author?.slice(0, 2).toUpperCase() || 'HF'}</span>
          {!avatarFailed && model.author && (
            <img
              src={`/api/models/huggingface/authors/${encodeURIComponent(model.author)}/avatar`}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={() => setAvatarFailed(true)}
              className="absolute inset-0 h-full w-full bg-[#111118] object-cover"
            />
          )}
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-theme-text">{model.id}</h3>
            {model.gated && (
              <span className="inline-flex items-center gap-1 rounded border border-theme-border bg-theme-text-secondary/10 px-1.5 py-0.5 text-[10px] font-semibold text-theme-text-secondary">
                <LockKeyhole size={10} /> Gated
              </span>
            )}
            {model.runtimeCompatible === false && (
              <span className="inline-flex items-center gap-1 rounded border border-theme-border bg-theme-text-secondary/10 px-1.5 py-0.5 text-[10px] font-semibold text-theme-text-secondary">
                Browse only
              </span>
            )}
          </div>
          <p className="mt-1 truncate text-[11px] text-theme-text-muted">
            {formatPipeline(model.pipelineTag)} · Updated {formatDate(model.lastModified)}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs font-medium tabular-nums text-theme-text-secondary">
        <span className="inline-flex min-w-[54px] items-center gap-1.5"><ArrowDownToLine size={12} /> {formatCompact(model.downloads)}</span>
        <span className="inline-flex items-center gap-1.5"><Heart size={12} /> {formatCompact(model.likes)}</span>
      </div>
      <div className="text-xs text-theme-text-secondary">{formatLicense(model.license)}</div>
      <div>
        <span className="inline-flex items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.035] px-2 py-1 text-[10px] font-semibold text-theme-text-secondary">
          <FileArchive size={11} /> {model.ggufFileCount || '—'} GGUF
        </span>
      </div>
      <button
        type="button"
        onClick={onInspect}
        className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-theme-border bg-theme-text-secondary/8 px-3 text-xs font-semibold text-theme-text-secondary transition-colors hover:border-theme-border hover:bg-theme-text-secondary/15"
      >
        <Box size={13} /> {model.runtimeCompatible === false ? 'Inspect' : 'Choose file'}
      </button>
    </div>
  )
}

function ArtifactDialog({ model, details, loading, error, preflight, preflightLoading, preflightError, gpu, downloadBusy, importingArtifact, importStatus, onClose, onImport, onRetry }) {
  const refusal = preflight?.refusal || null
  const [artifactFilter, setArtifactFilter] = useState('')
  const filteredArtifacts = useMemo(() => {
    const query = artifactFilter.trim().toLowerCase()
    return (details?.artifacts || []).filter(artifact => (
      artifact.label.toLowerCase().includes(query)
      || (artifact.quantization || '').toLowerCase().includes(query)
    ))
  }, [details, artifactFilter])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={`Choose a GGUF from ${model.id}`}>
      <div className="max-h-[88vh] w-full max-w-5xl overflow-hidden rounded-lg border border-white/[0.1] bg-[#090910] shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-white/[0.07] px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-base font-semibold text-theme-text">{model.id}</h2>
              <span className="rounded border border-theme-border bg-theme-text-secondary/10 px-1.5 py-0.5 text-[10px] font-semibold text-theme-text-secondary">Hugging Face</span>
            </div>
            <p className="mt-1 text-xs text-theme-text-muted">Select an exact, integrity-qualified GGUF artifact.</p>
          </div>
          <button type="button" onClick={onClose} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/[0.08] text-theme-text-muted hover:text-theme-text" title="Close">
            <X size={15} />
          </button>
        </header>

        <div className="max-h-[calc(88vh-72px)] overflow-y-auto p-5">
          {importStatus}
          {loading && (
            <div className="flex min-h-52 items-center justify-center gap-3 text-sm text-theme-text-muted">
              <Loader2 size={18} className="animate-spin text-theme-text-secondary" /> Reading repository metadata...
            </div>
          )}
          {error && (
            <div role="alert" className="flex flex-col gap-3 rounded-lg border border-red-400/25 bg-red-500/10 px-4 py-3 text-sm text-red-300 sm:flex-row sm:items-center sm:justify-between">
              <span>{error}</span>
              <button type="button" onClick={onRetry} disabled={loading} className="inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-md border border-red-300/25 bg-red-400/10 px-3 text-xs font-semibold text-red-100 transition-colors hover:bg-red-400/20 disabled:opacity-50">
                <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Retry details
              </button>
            </div>
          )}
          {details && (
            <>
              <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <Metric icon={ShieldCheck} label="License" value={formatLicense(details.license)} />
                <Metric
                  icon={Gauge}
                  label="Context metadata"
                  value={formatContext(preflight?.contextLength || details.contextLength)}
                  detail={contextSourceLabel(preflight?.contextLength ? preflight.contextSource : details.contextSource)}
                />
                <Metric icon={HardDrive} label="Available artifacts" value={`${details.artifacts.length} choices`} />
                <Metric icon={CheckCircle2} label="Pinned revision" value={details.sha?.slice(0, 10) || 'Unknown'} mono />
              </div>

              {details.runtimeCompatible === false && (
                <div className="mb-4 rounded-lg border border-theme-border bg-theme-text-secondary/8 px-4 py-3 text-sm text-theme-text-secondary">
                  <p>{details.runtimeReason}. You can inspect its artifacts here, but ODS will not route it through the LLM runtime.</p>
                  <HelpLink className="mt-1 text-xs" />
                </div>
              )}

              {details.runtimeCompatible !== false && details.artifacts.length > 0 && (
                <PreflightSummary preflight={preflight} loading={preflightLoading} error={preflightError} />
              )}

              {details.artifacts.length === 0 ? (
                <div className="rounded-lg border border-theme-border bg-theme-text-secondary/8 px-4 py-8 text-center text-sm text-theme-text-secondary">
                  This repository has no complete GGUF artifact with exact size and SHA-256 metadata.
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-white/[0.08]">
                  <div className="flex flex-wrap items-center gap-3 border-b border-white/[0.06] px-4 py-3">
                    <input
                      type="search"
                      aria-label="Filter GGUF artifacts"
                      placeholder="Filename or quantization"
                      maxLength={200}
                      value={artifactFilter}
                      onChange={event => setArtifactFilter(event.target.value)}
                      className="min-w-0 flex-1 rounded-md border border-white/[0.12] bg-black/20 px-3 py-2 text-sm text-theme-text focus:border-theme-accent focus:outline-none"
                    />
                    <span role="status" className="text-xs text-theme-text-muted">{filteredArtifacts.length} of {details.artifacts.length} artifacts</span>
                    {artifactFilter && (
                      <button type="button" onClick={() => setArtifactFilter('')} className="text-xs text-theme-text-secondary hover:text-theme-text-secondary" aria-label="Clear artifact filter">Clear</button>
                    )}
                  </div>
                  <div className="hidden grid-cols-[minmax(220px,1fr)_100px_120px_130px_130px] gap-4 border-b border-white/[0.06] bg-black/20 px-4 py-2.5 text-[9px] font-semibold uppercase tracking-[0.15em] text-theme-text-muted/60 lg:grid">
                    <span>Artifact</span><span>Quant</span><span>Download</span><span>Memory estimate</span><span>Action</span>
                  </div>
                  <div className="divide-y divide-white/[0.05]">
                    {filteredArtifacts.length === 0 && <p className="px-4 py-8 text-center text-sm text-theme-text-muted">No artifacts match this filter.</p>}
                    {filteredArtifacts.map(artifact => (
                      <ArtifactRow
                        key={artifact.id}
                        artifact={artifact}
                        gpu={gpu}
                        check={preflight?.artifacts?.[artifact.id] || null}
                        checking={preflightLoading}
                        refusal={refusal}
                        busy={downloadBusy || Boolean(importingArtifact)}
                        importing={importingArtifact === artifact.id}
                        runtimeCompatible={details.runtimeCompatible !== false}
                        onImport={(options) => onImport(artifact, options)}
                      />
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-4 flex flex-col gap-3 border-t border-white/[0.06] pt-4 text-xs text-theme-text-muted sm:flex-row sm:items-center sm:justify-between">
                <p>Community models are not included in the ODS compatibility matrix until benchmarked locally.</p>
                <a href={details.url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1.5 text-theme-text-secondary hover:text-theme-text-secondary">
                  View model card <ExternalLink size={12} />
                </a>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ArtifactRow({ artifact, gpu, check, checking, refusal, busy, importing, runtimeCompatible, onImport }) {
  const [confirmingOverride, setConfirmingOverride] = useState(false)
  const memory = artifactMemory(artifact, gpu, check, checking)
  const diskShort = check?.disk === 'insufficient' && !artifact.installed
  const blocked = !runtimeCompatible || (refusal && !refusal.overridable)
  const needsOverride = Boolean(refusal?.overridable) && !blocked
  const label = importing ? 'Starting'
    : artifact.installed ? 'Installed'
      : blocked ? (refusal?.code === 'gated' ? 'Needs access' : 'Not supported')
        : diskShort ? 'Not enough disk'
          : needsOverride ? 'Import anyway…'
            : artifact.importedModelId ? 'Retry' : 'Import'
  const startImport = () => {
    if (needsOverride) {
      setConfirmingOverride(true)
      return
    }
    onImport()
  }
  return (
    <div className="grid grid-cols-2 gap-3 px-4 py-3.5 lg:grid-cols-[minmax(220px,1fr)_100px_120px_130px_130px] lg:items-center lg:gap-4">
      <div className="col-span-2 min-w-0 lg:col-span-1">
        <p className="truncate text-xs font-semibold text-theme-text" title={artifact.label}>{artifact.label}</p>
        <p className="mt-1 text-[10px] text-theme-text-muted">{artifact.split ? `${artifact.files.length} verified parts` : 'Single verified file'}</p>
      </div>
      <span className="text-xs font-semibold text-theme-text-secondary">{artifact.quantization || 'Unknown'}</span>
      <span className="font-mono text-xs text-theme-text-secondary">{formatBytes(artifact.sizeBytes)}</span>
      <div>
        <p className={`text-xs font-semibold ${memory.ok === false ? 'text-theme-text-secondary' : 'text-emerald-300'}`}>{memory.value}</p>
        <p className="mt-0.5 text-[10px] text-theme-text-muted">{memory.detail}</p>
        {diskShort && <p className="mt-0.5 text-[10px] font-semibold text-amber-300">Not enough free disk space</p>}
      </div>
      <button type="button" onClick={startImport} disabled={busy || artifact.installed || blocked || diskShort} className="inline-flex h-8 items-center justify-center gap-2 rounded-md bg-theme-accent px-3 text-xs font-semibold text-white transition-colors hover:bg-theme-accent-light disabled:cursor-not-allowed disabled:opacity-45">
        {importing ? <Loader2 size={13} className="animate-spin" /> : artifact.installed ? <CheckCircle2 size={13} /> : <ArrowDownToLine size={13} />}
        {label}
      </button>
      {confirmingOverride && (
        <div role="alertdialog" aria-label="Import anyway" className="col-span-2 rounded-lg border border-amber-400/25 bg-amber-500/10 p-3 text-xs text-amber-100 lg:col-span-5">
          <p>{refusal.message}</p>
          <p className="mt-1">Loading it will most likely fail, and ODS will then switch back to your current model.</p>
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={() => { setConfirmingOverride(false); onImport({ allowUnsupportedRuntime: true }) }} disabled={busy} className="rounded border border-amber-300/40 px-3 py-1 font-semibold disabled:opacity-50">
              Import anyway
            </button>
            <button type="button" onClick={() => setConfirmingOverride(false)} className="rounded border border-white/[0.12] px-3 py-1">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function artifactMemory(artifact, gpu, check, checking) {
  const fit = check?.fit
  if (fit && fit.status !== 'unknown' && Number(fit.requiredGb) > 0) {
    const required = `~${Number(fit.requiredGb).toFixed(1)} GB`
    const context = formatContext(fit.contextLength)
    const rough = fit.estimate === 'rough' ? ' (rough estimate)' : ''
    if (fit.status === 'fits') return { value: required, detail: `Fits at ${context}${rough}`, ok: true }
    if (fit.status === 'fits_short_context') return { value: required, detail: `Fits at ${context}; ODS Talk needs 64K${rough}`, ok: true }
  }
  if (fit?.status === 'too_large') {
    return { value: 'Too large', detail: 'Exceeds this machine’s model memory', ok: false }
  }
  if (checking && !fit) return { value: 'Checking…', detail: 'Reading the model header', ok: null }
  const sizeGb = artifact.sizeBytes / (1024 ** 3)
  const estimatedVram = sizeGb + Math.min(Math.max(sizeGb * 0.18, 0.5), 3.5)
  const totalVram = Number(gpu?.vramTotal || 0)
  const fits = totalVram > 0 ? estimatedVram <= totalVram + 0.25 : null
  return {
    value: `~${estimatedVram.toFixed(1)} GB`,
    detail: fits === null ? 'GPU unknown' : fits ? 'Fits detected GPU' : 'Exceeds GPU VRAM',
    ok: fits,
  }
}

const THINKING_TEXT = {
  toggle: 'Thinking can be turned off',
  effort: 'Thinking has effort levels',
  tags: 'Thinks; it may not turn off',
  none: 'No thinking markers',
}

function PreflightSummary({ preflight, loading, error }) {
  if (loading && !preflight) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-white/[0.07] bg-black/20 px-4 py-3 text-xs text-theme-text-muted" aria-live="polite">
        <Loader2 size={13} className="animate-spin" /> Checking this repository before download…
      </div>
    )
  }
  if (error && !preflight) {
    return (
      <div className="mb-4 rounded-lg border border-white/[0.07] bg-black/20 px-4 py-3 text-xs text-theme-text-muted" aria-live="polite">
        Could not check this repository before download ({error}). You can still import; ODS verifies the model when you switch to it.
      </div>
    )
  }
  if (!preflight) return null
  const { refusal, runtime, template, header } = preflight
  const architecture = preflight.architecture || 'unknown'
  const supported = runtime?.architectureSupported
  return (
    <section aria-label="Before you download" className="mb-4 space-y-2 rounded-lg border border-white/[0.07] bg-black/20 px-4 py-3 text-xs">
      {refusal && (
        <div role="alert" className={`flex items-start gap-2 rounded-md border px-3 py-2 ${refusal.overridable ? 'border-amber-400/25 bg-amber-500/10 text-amber-100' : 'border-red-400/25 bg-red-500/10 text-red-200'}`}>
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <div>
            <p>{refusal.message}</p>
            <HelpLink className="mt-1 text-[11px]" />
          </div>
        </div>
      )}
      <p className="flex items-center gap-2 text-theme-text-secondary">
        <Cpu size={12} />
        {supported === true && `Architecture ${architecture} runs on this machine’s llama.cpp (${runtime.build}).`}
        {supported === false && `Architecture ${architecture} is not in this machine’s llama.cpp (${runtime.build}).`}
        {supported == null && (preflight.modelKind === 'chat'
          ? `Architecture ${architecture}: runtime support could not be checked.`
          : `Not a chat model (${preflight.modelKind}).`)}
      </p>
      {template?.present === false && (
        <p className="text-theme-text-secondary">No chat template in this file: chat quality and tool use are unlikely to work.</p>
      )}
      {template?.present === true && (
        <p className="text-theme-text-secondary">
          {template.tools ? 'Its template describes tool calls' : 'Its template does not describe tool calls, so Pixel cannot use tools with it'}
          {' · '}{THINKING_TEXT[template.thinking] || 'Thinking unknown'}
        </p>
      )}
      {header?.status === 'unavailable' && header.message && refusal?.code !== 'gated' && (
        <p className="text-theme-text-muted">Some checks could not run: {header.message}.</p>
      )}
      <p className="text-[10px] text-theme-text-muted">From the repository’s own files. How a model actually behaves shows only once you use it.</p>
    </section>
  )
}

function Metric({ icon: Icon, label, value, detail, mono = false }) {
  return (
    <div className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-3">
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.13em] text-theme-text-muted/65"><Icon size={12} /> {label}</div>
      <p className={`mt-2 truncate text-xs font-semibold text-theme-text ${mono ? 'font-mono' : ''}`}>{value}</p>
      {detail && <p className="mt-1 text-[10px] text-theme-text-muted">{detail}</p>}
    </div>
  )
}

function RepositorySkeleton() {
  return Array.from({ length: 6 }, (_, index) => (
    <div key={index} className="grid animate-pulse grid-cols-[minmax(0,1fr)_140px] gap-4 px-5 py-4">
      <div className="h-9 rounded bg-white/[0.04]" />
      <div className="h-8 rounded bg-white/[0.04]" />
    </div>
  ))
}

async function responseJson(response) {
  try { return await response.json() } catch { return {} }
}

function errorMessage(body, fallback) {
  if (typeof body?.detail === 'string') return body.detail
  if (typeof body?.detail?.message === 'string') return body.detail.message
  if (typeof body?.error === 'string') return body.error
  return fallback
}

function formatCompact(value) {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value || 0))
}

function formatDate(value) {
  if (!value) return 'unknown'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'unknown'
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric' }).format(date)
}

function formatLicense(value) {
  return value ? String(value).replace(/-/g, ' ').toUpperCase() : 'Not declared'
}

function formatPipeline(value) {
  return String(value || 'text-generation').replace(/-/g, ' ')
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes >= 1024 ** 3) return `${(bytes / (1024 ** 3)).toFixed(1)} GB`
  return `${Math.round(bytes / (1024 ** 2))} MB`
}

function formatContext(value) {
  const tokens = Number(value || 0)
  return tokens ? `${Math.round(tokens / 1024)}K tokens` : 'Unknown'
}

function contextSourceLabel(source) {
  if (source === 'gguf_header') return 'This file’s GGUF header'
  if (source === 'gguf_metadata') return 'GGUF metadata'
  if (source === 'hub_config') return 'Hub config'
  return 'Not published by repository'
}

function authorFallbackStyle(author) {
  const value = String(author || 'huggingface')
  const hash = Array.from(value).reduce((total, char) => ((total * 31) + char.charCodeAt(0)) >>> 0, 0)
  const hue = hash % 360
  return {
    backgroundColor: `hsla(${hue}, 65%, 48%, 0.14)`,
    borderColor: `hsla(${hue}, 72%, 62%, 0.32)`,
    color: `hsl(${hue}, 82%, 76%)`,
  }
}
