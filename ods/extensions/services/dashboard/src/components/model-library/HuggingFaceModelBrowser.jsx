import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDownToLine,
  Box,
  CheckCircle2,
  Cloud,
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

const SEARCH_DELAY_MS = 350

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
  const [searchAttempt, setSearchAttempt] = useState(0)
  const detailsRequestRef = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    const timeout = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q: query.trim(), sort, limit: '20' })
        const response = await fetch(`/api/models/huggingface/search?${params}`, { signal: controller.signal })
        const body = await responseJson(response)
        if (!response.ok) throw new Error(errorMessage(body, 'Hugging Face search failed'))
        setResults(Array.isArray(body.models) ? body.models : [])
        setAuthenticated(Boolean(body.authenticated))
        setStale(Boolean(body.stale))
      } catch (requestError) {
        if (requestError?.name !== 'AbortError') setError(requestError.message)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }, SEARCH_DELAY_MS)
    return () => {
      clearTimeout(timeout)
      controller.abort()
    }
  }, [query, sort, searchAttempt])

  const openRepository = async (model) => {
    const requestId = detailsRequestRef.current + 1
    detailsRequestRef.current = requestId
    setSelectedRepo(model)
    setDetails(null)
    setDetailsError(null)
    setDetailsLoading(true)
    try {
      const response = await fetch(`/api/models/huggingface/repositories/${encodeURI(model.id)}`)
      const body = await responseJson(response)
      if (!response.ok) throw new Error(errorMessage(body, 'Could not inspect this repository'))
      if (detailsRequestRef.current !== requestId) return
      setDetails(body)
    } catch (requestError) {
      if (detailsRequestRef.current === requestId) setDetailsError(requestError.message)
    } finally {
      if (detailsRequestRef.current === requestId) setDetailsLoading(false)
    }
  }

  const closeRepository = () => {
    if (importingArtifact) return
    detailsRequestRef.current += 1
    setSelectedRepo(null)
    setDetails(null)
    setDetailsError(null)
  }

  const importArtifact = async (artifact) => {
    if (!details?.id || downloadBusy || importingArtifact) return
    setImportingArtifact(artifact.id)
    setDetailsError(null)
    try {
      const response = await fetch('/api/models/huggingface/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repoId: details.id, artifactId: artifact.id }),
      })
      const body = await responseJson(response)
      if (!response.ok) throw new Error(errorMessage(body, 'Could not start the GGUF import'))
      await onImportStarted?.(body)
      setSelectedRepo(null)
      setDetails(null)
    } catch (requestError) {
      setDetailsError(requestError.message)
    } finally {
      setImportingArtifact(null)
    }
  }

  return (
    <div className="space-y-4">
      <section className="grid gap-3 border-b border-white/[0.06] pb-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-amber-300/20 bg-amber-300/8">
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
                <span className="inline-flex items-center gap-1 rounded border border-amber-400/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
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
            {loading && <Loader2 size={11} className="animate-spin text-amber-300" />}
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
            className="h-10 w-full rounded-lg border border-white/[0.08] bg-black/25 pl-10 pr-10 text-sm text-theme-text outline-none transition-colors placeholder:text-theme-text-muted/60 focus:border-amber-300/40"
            aria-busy={loading}
          />
          {loading && <Loader2 size={15} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-amber-300" />}
        </label>
        <select
          value={sort}
          onChange={(event) => setSort(event.target.value)}
          className="h-10 rounded-lg border border-white/[0.08] bg-[#0b0b12] px-3 text-xs text-theme-text-secondary outline-none focus:border-amber-300/40"
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
          <div className="absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden bg-amber-300/10">
            <div className="h-full w-full animate-pulse bg-gradient-to-r from-transparent via-amber-300 to-transparent" />
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
          {!loading && results.length === 0 && (
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
          model={selectedRepo}
          details={details}
          loading={detailsLoading}
          error={detailsError}
          gpu={gpu}
          downloadBusy={downloadBusy}
          importingArtifact={importingArtifact}
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
    <div className="grid grid-cols-2 gap-3 px-4 py-4 transition-colors hover:bg-white/[0.025] sm:grid-cols-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(280px,1.5fr)_120px_100px_110px_140px] lg:items-center lg:gap-4 lg:px-5 lg:py-3.5">
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
              <span className="inline-flex items-center gap-1 rounded border border-amber-400/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                <LockKeyhole size={10} /> Gated
              </span>
            )}
            {model.runtimeCompatible === false && (
              <span className="inline-flex items-center gap-1 rounded border border-amber-400/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
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
        className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-amber-300/25 bg-amber-300/8 px-3 text-xs font-semibold text-amber-100 transition-colors hover:border-amber-200/45 hover:bg-amber-300/15"
      >
        <Box size={13} /> {model.runtimeCompatible === false ? 'Inspect' : 'Choose file'}
      </button>
    </div>
  )
}

function ArtifactDialog({ model, details, loading, error, gpu, downloadBusy, importingArtifact, onClose, onImport, onRetry }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={`Choose a GGUF from ${model.id}`}>
      <div className="max-h-[88vh] w-full max-w-5xl overflow-hidden rounded-lg border border-white/[0.1] bg-[#090910] shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-white/[0.07] px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-base font-semibold text-theme-text">{model.id}</h2>
              <span className="rounded border border-amber-300/25 bg-amber-300/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-200">Hugging Face</span>
            </div>
            <p className="mt-1 text-xs text-theme-text-muted">Select an exact, integrity-qualified GGUF artifact.</p>
          </div>
          <button type="button" onClick={onClose} disabled={Boolean(importingArtifact)} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/[0.08] text-theme-text-muted hover:text-theme-text disabled:opacity-40" title="Close">
            <X size={15} />
          </button>
        </header>

        <div className="max-h-[calc(88vh-72px)] overflow-y-auto p-5">
          {loading && (
            <div className="flex min-h-52 items-center justify-center gap-3 text-sm text-theme-text-muted">
              <Loader2 size={18} className="animate-spin text-amber-300" /> Reading repository metadata...
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
                <Metric icon={Gauge} label="Context metadata" value={formatContext(details.contextLength)} detail={details.contextSource === 'hub_config' ? 'Hub config' : 'ODS fallback'} />
                <Metric icon={HardDrive} label="Available artifacts" value={`${details.artifacts.length} choices`} />
                <Metric icon={CheckCircle2} label="Pinned revision" value={details.sha?.slice(0, 10) || 'Unknown'} mono />
              </div>

              {details.runtimeCompatible === false && (
                <div className="mb-4 rounded-lg border border-amber-400/20 bg-amber-500/8 px-4 py-3 text-sm text-amber-200">
                  {details.runtimeReason}. You can inspect its artifacts here, but ODS will not route it through the LLM runtime.
                </div>
              )}

              {details.artifacts.length === 0 ? (
                <div className="rounded-lg border border-amber-400/20 bg-amber-500/8 px-4 py-8 text-center text-sm text-amber-200">
                  This repository has no complete GGUF artifact with exact size and SHA-256 metadata.
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-white/[0.08]">
                  <div className="hidden grid-cols-[minmax(220px,1fr)_100px_120px_130px_130px] gap-4 border-b border-white/[0.06] bg-black/20 px-4 py-2.5 text-[9px] font-semibold uppercase tracking-[0.15em] text-theme-text-muted/60 lg:grid">
                    <span>Artifact</span><span>Quant</span><span>Download</span><span>Memory estimate</span><span>Action</span>
                  </div>
                  <div className="divide-y divide-white/[0.05]">
                    {details.artifacts.map(artifact => (
                      <ArtifactRow
                        key={artifact.id}
                        artifact={artifact}
                        gpu={gpu}
                        busy={downloadBusy || Boolean(importingArtifact)}
                        importing={importingArtifact === artifact.id}
                        runtimeCompatible={details.runtimeCompatible !== false}
                        onImport={() => onImport(artifact)}
                      />
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-4 flex flex-col gap-3 border-t border-white/[0.06] pt-4 text-xs text-theme-text-muted sm:flex-row sm:items-center sm:justify-between">
                <p>Community models are not included in the ODS compatibility matrix until benchmarked locally.</p>
                <a href={details.url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1.5 text-amber-300 hover:text-amber-200">
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

function ArtifactRow({ artifact, gpu, busy, importing, runtimeCompatible, onImport }) {
  const sizeGb = artifact.sizeBytes / (1024 ** 3)
  const estimatedVram = sizeGb + Math.min(Math.max(sizeGb * 0.18, 0.5), 3.5)
  const totalVram = Number(gpu?.vramTotal || 0)
  const fits = totalVram > 0 ? estimatedVram <= totalVram + 0.25 : null
  return (
    <div className="grid grid-cols-2 gap-3 px-4 py-3.5 lg:grid-cols-[minmax(220px,1fr)_100px_120px_130px_130px] lg:items-center lg:gap-4">
      <div className="col-span-2 min-w-0 lg:col-span-1">
        <p className="truncate text-xs font-semibold text-theme-text" title={artifact.label}>{artifact.label}</p>
        <p className="mt-1 text-[10px] text-theme-text-muted">{artifact.split ? `${artifact.files.length} verified parts` : 'Single verified file'}</p>
      </div>
      <span className="text-xs font-semibold text-theme-text-secondary">{artifact.quantization || 'Unknown'}</span>
      <span className="font-mono text-xs text-theme-text-secondary">{formatBytes(artifact.sizeBytes)}</span>
      <div>
        <p className={`text-xs font-semibold ${fits === false ? 'text-amber-300' : 'text-emerald-300'}`}>~{estimatedVram.toFixed(1)} GB</p>
        <p className="mt-0.5 text-[10px] text-theme-text-muted">{fits === null ? 'GPU unknown' : fits ? 'Fits detected GPU' : 'Exceeds GPU VRAM'}</p>
      </div>
      <button type="button" onClick={onImport} disabled={busy || artifact.installed || !runtimeCompatible} className="inline-flex h-8 items-center justify-center gap-2 rounded-md bg-theme-accent px-3 text-xs font-semibold text-white transition-colors hover:bg-theme-accent-light disabled:cursor-not-allowed disabled:opacity-45">
        {importing ? <Loader2 size={13} className="animate-spin" /> : artifact.installed ? <CheckCircle2 size={13} /> : <ArrowDownToLine size={13} />}
        {importing ? 'Starting' : artifact.installed ? 'Installed' : !runtimeCompatible ? 'Not supported' : artifact.importedModelId ? 'Retry' : 'Import'}
      </button>
    </div>
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
