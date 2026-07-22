import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Box,
  ChevronLeft,
  ChevronRight,
  Download,
  HardDrive,
  Library,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { useModels } from '../hooks/useModels'
import { useDownloadProgress } from '../hooks/useDownloadProgress'
import HuggingFaceModelBrowser from '../components/model-library/HuggingFaceModelBrowser'

const PAGE_SIZE = 10
const DOWNLOAD_STATUS_TIMEOUT_MS = 15000
const MAX_SINGLE_REQUEST_TOKENS_PER_SECOND = 10000
const TECH_PANEL_STYLE = {
  background: 'linear-gradient(180deg, rgba(10,10,18,0.96), rgba(7,7,13,0.92))',
  borderColor: 'rgba(255,255,255,0.08)',
  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.035), 0 20px 60px rgba(0,0,0,0.22)',
}
const TECH_TILE_STYLE = {
  background: `
    linear-gradient(180deg, rgba(14,14,21,0.88), rgba(8,8,14,0.92)),
    repeating-linear-gradient(90deg, transparent 0 31px, rgba(255,255,255,0.028) 31px 32px),
    repeating-linear-gradient(180deg, transparent 0 31px, rgba(255,255,255,0.024) 31px 32px),
    radial-gradient(circle at 18% 0%, rgba(157,0,255,0.08), transparent 30%)
  `,
  borderColor: 'rgba(255,255,255,0.11)',
  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.045), inset 0 -18px 34px rgba(0,0,0,0.18)',
}

function catalogModelIdForProgress(models, progressModel) {
  const rawLabel = typeof progressModel === 'string' ? progressModel.trim() : ''
  if (!rawLabel) return null

  const rawToken = rawLabel.toLowerCase()
  const directMatch = models.find(model => String(model.id || '').toLowerCase() === rawToken)
  if (directMatch) return directMatch.id

  const fileToken = rawLabel.split(' (', 1)[0].split(/[\\/]/).pop()?.toLowerCase()
  if (!fileToken) return null
  const fileMatch = models.find(model => {
    if (String(model.gguf || '').toLowerCase() === fileToken) return true
    return Array.isArray(model.ggufParts) && model.ggufParts.some(
      part => String(part?.file || '').toLowerCase() === fileToken
    )
  })
  return fileMatch?.id ?? null
}

export default function Models() {
  const downloadProgress = useDownloadProgress()
  const {
    models,
    gpu,
    currentModel,
    configuredModel,
    odsMode,
    configuredMode,
    canActivateModels,
    activationModeError,
    recommendationAlternatives,
    hermesMinimumContext,
    loading,
    error,
    actionLoading,
    actionLoadingModels,
    activationLoading,
    downloadModel,
    loadModel,
    benchmarkModel,
    deleteModel,
    refresh,
  } = useModels()

  const [downloadStarting, setDownloadStarting] = useState(null)
  const [downloadAwaitingStatus, setDownloadAwaitingStatus] = useState(false)
  const [downloadStartFailure, setDownloadStartFailure] = useState(null)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [compatibilityFilter, setCompatibilityFilter] = useState('all')
  const [speedFilter, setSpeedFilter] = useState('any')
  const [contextFloor, setContextFloor] = useState(0)
  const [deleteConfirmModel, setDeleteConfirmModel] = useState(null)
  const [libraryScope, setLibraryScope] = useState('recommended')
  const libraryRef = useRef(null)

  useEffect(() => {
    const terminalProgress = downloadProgress.progress?.error ||
      ['failed', 'error', 'cancelled'].includes(downloadProgress.progress?.status)

    if (downloadProgress.isDownloading || terminalProgress) {
      setDownloadStarting(null)
      setDownloadAwaitingStatus(false)
    }
    if (downloadProgress.isDownloading || terminalProgress) {
      setDownloadStartFailure(null)
    }
  }, [downloadProgress.isDownloading, downloadProgress.progress])

  useEffect(() => {
    if (downloadProgress.completedDownload?.status === 'complete') {
      setDownloadStarting(null)
      setDownloadAwaitingStatus(false)
      setDownloadStartFailure(null)
      refresh()
    }
  }, [downloadProgress.completedDownload, refresh])

  useEffect(() => {
    if (!downloadStarting || !downloadAwaitingStatus) return undefined

    const modelId = downloadStarting
    const timeout = setTimeout(() => {
      setDownloadStarting(null)
      setDownloadAwaitingStatus(false)
      setDownloadStartFailure({
        modelId,
        error: `Download for ${modelId} did not start within 15 seconds. Check the service and retry.`,
      })
      void downloadProgress.refresh()
    }, DOWNLOAD_STATUS_TIMEOUT_MS)

    return () => clearTimeout(timeout)
  }, [downloadAwaitingStatus, downloadProgress.refresh, downloadStarting])

  useEffect(() => {
    setPage(1)
  }, [models.length])

  const activeModel = useMemo(() => {
    return models.find(model => model.status === 'loaded')
      || models.find(model => model.id === currentModel)
      || null
  }, [currentModel, models])

  const installedModels = useMemo(
    () => models.filter(model => ['downloaded', 'loaded'].includes(model.status)),
    [models]
  )
  const odsCatalogModels = useMemo(
    () => models
      .filter(model => model.metadata?.catalogSource !== 'huggingface')
      .sort((left, right) => Number(Boolean(right.recommended)) - Number(Boolean(left.recommended)) || Number(Boolean(right.fitsVram)) - Number(Boolean(left.fitsVram))),
    [models]
  )
  const scopedModels = libraryScope === 'installed' ? installedModels : odsCatalogModels
  const categoryOptions = useMemo(
    () => buildCategoryOptions(scopedModels, hermesMinimumContext),
    [hermesMinimumContext, scopedModels]
  )
  const maxContext = useMemo(
    () => Math.max(0, ...scopedModels.map(model => Number(model.contextLength || 0))),
    [scopedModels]
  )
  const modelInsights = useMemo(
    () => buildModelInsights(scopedModels),
    [scopedModels]
  )
  const filteredModels = useMemo(() => {
    const search = query.trim().toLowerCase()
    return scopedModels.filter(model => {
      const memory = getMemoryMeta(model, gpu)
      if (search && !matchesModelSearch(model, search)) return false
      if (categoryFilter !== 'all' && !getModelCategoryIds(model, hermesMinimumContext).includes(categoryFilter)) return false
      if (!matchesCompatibilityFilter(model, memory, compatibilityFilter)) return false
      if (!matchesSpeedFilter(model, speedFilter, hermesMinimumContext)) return false
      if (contextFloor > 0 && Number(model.contextLength || 0) < contextFloor) return false
      return true
    })
  }, [categoryFilter, compatibilityFilter, contextFloor, gpu, hermesMinimumContext, query, scopedModels, speedFilter])

  const pageCount = Math.max(1, Math.ceil(filteredModels.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const visibleModels = filteredModels.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)
  const startIndex = filteredModels.length ? (safePage - 1) * PAGE_SIZE + 1 : 0
  const endIndex = Math.min(safePage * PAGE_SIZE, filteredModels.length)

  useEffect(() => {
    setPage(1)
  }, [categoryFilter, compatibilityFilter, contextFloor, libraryScope, query, scopedModels.length, speedFilter])

  const handleDownload = async (modelId) => {
    setDownloadStartFailure(null)
    downloadProgress.clearTerminal?.()
    setDownloadAwaitingStatus(false)
    setDownloadStarting(modelId)
    try {
      await downloadModel(modelId)
      setDownloadAwaitingStatus(true)
      await downloadProgress.refresh()
    } catch (downloadError) {
      setDownloadStarting(null)
      setDownloadAwaitingStatus(false)
      setDownloadStartFailure({
        modelId,
        error: downloadError?.message || `Failed to start download for ${modelId}.`,
      })
    }
  }

  const handleConfirmDelete = async () => {
    if (!deleteConfirmModel?.id) return
    const modelId = deleteConfirmModel.id
    setDeleteConfirmModel(null)
    await deleteModel(modelId)
  }

  const handleHuggingFaceImportStarted = async (result) => {
    setDownloadStartFailure(null)
    setDownloadStarting(result?.modelId || null)
    setDownloadAwaitingStatus(true)
    await Promise.allSettled([
      Promise.resolve().then(() => downloadProgress.refresh()),
      Promise.resolve().then(() => refresh()),
    ])
  }

  const pendingModelActions = actionLoadingModels ?? (actionLoading ? [actionLoading] : [])
  const visibleDownloadProgress = downloadProgress.progress || (downloadStartFailure && {
    status: 'error',
    model: downloadStartFailure.modelId,
    error: downloadStartFailure.error,
  })
  const retryModelId = catalogModelIdForProgress(models, visibleDownloadProgress?.model)

  if (loading) {
    return (
      <div className="p-3 sm:p-6 lg:p-8">
        <div className="animate-pulse">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <div className="mb-3 h-7 w-36 rounded bg-theme-card" />
              <div className="h-4 w-72 rounded bg-theme-card" />
            </div>
            <div className="h-9 w-36 rounded-lg bg-theme-card" />
          </div>
          <div className="mb-4 h-24 rounded-xl bg-theme-card" />
          <div className="h-[520px] rounded-xl bg-theme-card" />
        </div>
      </div>
    )
  }

  return (
    <div className="p-3 sm:p-6 lg:p-8">
      <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-theme-text">Models</h1>
          <p className="mt-1 text-sm text-theme-text-muted">
            Discover, filter, and deploy the right model for your workflow.
          </p>
        </div>

        <div className="flex max-w-full flex-wrap items-center gap-2">
          <span className="inline-flex min-h-9 max-w-full items-center rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-center text-xs font-medium text-theme-text-secondary">
            Runtime: {formatModeLabel(odsMode)}
            {configuredMode !== odsMode ? ` / configured ${formatModeLabel(configuredMode)}` : ''}
          </span>
          <button
            type="button"
            onClick={() => libraryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-white/[0.08] bg-black/20 px-3 text-xs font-medium text-theme-text-secondary transition-colors hover:border-theme-accent/35 hover:text-theme-text"
          >
            <Library size={14} />
            Model Library
          </button>
          <button
            type="button"
            onClick={refresh}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] bg-black/20 text-theme-text-muted transition-colors hover:border-theme-accent/35 hover:text-theme-text"
            title="Refresh models"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </header>

      {error && (
        <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {!canActivateModels && (
        <section className="mb-5 flex flex-col gap-3 rounded-xl border border-amber-400/25 bg-amber-500/10 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <AlertCircle size={18} className="mt-0.5 shrink-0 text-amber-300" />
            <div>
              <p className="text-sm font-semibold text-amber-100">Local model runtime unavailable</p>
              <p className="mt-1 text-sm text-amber-100/75">{activationModeError}</p>
              <p className="mt-1 text-xs text-amber-100/60">Model downloads and deletion remain available.</p>
            </div>
          </div>
          <Link
            to="/settings"
            className="inline-flex h-8 shrink-0 items-center justify-center rounded-md border border-amber-200/20 bg-black/20 px-3 text-xs font-semibold text-amber-100 transition-colors hover:border-amber-200/40"
          >
            Review runtime settings
          </Link>
        </section>
      )}

      {visibleDownloadProgress && (
        <DownloadProgressBar
          progress={visibleDownloadProgress}
          helpers={downloadProgress}
          onRetry={retryModelId
            ? () => handleDownload(retryModelId)
            : null}
        />
      )}

      <CurrentModelPanel
        model={activeModel}
        currentModel={currentModel}
        gpu={gpu}
      />

      {!currentModel && configuredModel && (
        <section className="mb-4 rounded-xl border border-amber-400/25 bg-amber-500/10 p-4">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-amber-200">
              <AlertCircle size={14} className="mr-2 inline" />
              Selected during install: <strong>{configuredModel}</strong>. Run a benchmark after first launch for local tok/s.
            </div>
            {recommendationAlternatives.length > 0 && (
              <div className="text-xs text-amber-100/70">
                Top catalog fit: {recommendationAlternatives.slice(0, 3).map(item => item.name).join(' / ')}
              </div>
            )}
          </div>
        </section>
      )}

      <ModelSourceTabs
        value={libraryScope}
        onChange={setLibraryScope}
        installedCount={installedModels.length}
        recommendedCount={odsCatalogModels.length}
      />

      {libraryScope === 'huggingface' ? (
        <section
          ref={libraryRef}
          className="rounded-lg border p-4 sm:p-5"
          style={TECH_PANEL_STYLE}
        >
          <HuggingFaceModelBrowser
            gpu={gpu}
            downloadBusy={downloadProgress.isDownloading || Boolean(downloadStarting)}
            onImportStarted={handleHuggingFaceImportStarted}
          />
        </section>
      ) : (
      <div className="grid gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
        <ModelsFilterPanel
          query={query}
          setQuery={setQuery}
          categoryFilter={categoryFilter}
          setCategoryFilter={setCategoryFilter}
          categoryOptions={categoryOptions}
          compatibilityFilter={compatibilityFilter}
          setCompatibilityFilter={setCompatibilityFilter}
          speedFilter={speedFilter}
          setSpeedFilter={setSpeedFilter}
          contextFloor={contextFloor}
          setContextFloor={setContextFloor}
          maxContext={maxContext}
          insights={modelInsights}
          onReset={() => {
            setQuery('')
            setCategoryFilter('all')
            setCompatibilityFilter('all')
            setSpeedFilter('any')
            setContextFloor(0)
          }}
        />

        <section
          ref={libraryRef}
          className="overflow-hidden rounded-xl border"
          style={TECH_PANEL_STYLE}
        >
          <div className="min-w-full overflow-x-auto">
            <div className="lg:min-w-[1034px]">
              <div className="hidden grid-cols-[minmax(250px,1.7fr)_144px_70px_110px_120px_90px_130px] gap-5 border-b border-white/[0.055] px-5 py-3 text-[9px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/55 lg:grid">
                <span>Model</span>
                <span>Actions</span>
                <span>Size</span>
                <span>VRAM</span>
                <span>Speed</span>
                <span>Context</span>
                <span>Compatibility</span>
              </div>

              <div className="divide-y divide-white/[0.04]">
                {visibleModels.map((model, index) => {
                  const rowId = `${model.id || model.name || 'model'}:${startIndex + index}`
                  return (
                    <ModelTableRow
                      key={rowId}
                      model={model}
                      gpu={gpu}
                      canActivateModels={canActivateModels}
                      activationModeError={activationModeError}
                      hermesMinimumContext={hermesMinimumContext}
                      isCurrentModel={model.id === currentModel}
                      isLoading={pendingModelActions.includes(model.id)}
                      loadBusy={pendingModelActions.length > 0}
                      activationBusy={Boolean(activationLoading)}
                      downloadBusy={downloadProgress.isDownloading || !!downloadStarting}
                      downloadStarting={downloadStarting === model.id}
                      onDownload={() => handleDownload(model.id)}
                      onLoad={() => loadModel(model.id)}
                      onBenchmark={() => benchmarkModel(model.id)}
                      onDelete={() => setDeleteConfirmModel(model)}
                    />
                  )
                })}
              </div>

              {filteredModels.length === 0 && (
                <div className="px-5 py-12 text-center text-sm text-theme-text-muted">
                  No models match the current filters.
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-3 border-t border-white/[0.055] px-4 py-3 text-xs text-theme-text-muted sm:flex-row sm:items-center sm:justify-between">
            <span>
              Showing {startIndex}-{endIndex} of {filteredModels.length} models
            </span>
            <Pagination page={safePage} pageCount={pageCount} onChange={setPage} />
          </div>
        </section>
      </div>
      )}

      {deleteConfirmModel && (
        <DeleteModelDialog
          model={deleteConfirmModel}
          onCancel={() => setDeleteConfirmModel(null)}
          onConfirm={handleConfirmDelete}
        />
      )}
    </div>
  )
}

function CurrentModelPanel({ model, currentModel, gpu }) {
  const modelLabel = currentModel || model?.id
  const speed = getSpeedDisplay(model)
  const context = model ? formatContext(model.contextLength) : '--'
  const memory = model ? getMemoryMeta(model, gpu) : null
  const statusLabel = currentModel ? 'Currently running' : 'Model runtime'

  return (
    <section className="mb-4 rounded-xl border p-4" style={TECH_TILE_STYLE}>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px_150px] lg:items-center">
        <div className="flex min-w-0 items-center gap-4">
          <ModelPublisherIcon model={model} size="large" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-sm font-semibold text-theme-text sm:text-base">
                {statusLabel}: {modelLabel || 'none'}
              </h2>
              {model?.quantization && <Badge>{model.quantization}</Badge>}
              {model?.fitsVram && <Badge tone="green">{model.fitLabel || 'Fits GPU'}</Badge>}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-theme-text-muted">
              <span>{currentModel ? 'Active runtime' : 'Ready after first launch'}</span>
              {memory && <span>{memory.label} VRAM ({memory.percent}%)</span>}
              <span>{context} context</span>
            </div>
          </div>
        </div>

        <ModelSpeedVisual model={model} speed={speed} compact />

        <Link
          to="/"
          className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-white/[0.08] bg-black/20 px-3 text-xs font-semibold text-theme-text transition-colors hover:border-theme-accent/35 hover:bg-theme-accent/10"
        >
          Dashboard
        </Link>
      </div>
    </section>
  )
}

function ModelSourceTabs({ value, onChange, installedCount, recommendedCount }) {
  const tabs = [
    {
      id: 'installed',
      label: 'Installed',
      detail: 'Local files',
      count: installedCount,
      icon: HardDrive,
      tone: 'emerald',
    },
    {
      id: 'recommended',
      label: 'ODS Recommended',
      detail: 'Curated catalog',
      count: recommendedCount,
      image: '/osmantic-os-icon-192.png',
      tone: 'purple',
    },
    {
      id: 'huggingface',
      label: 'Hugging Face',
      detail: 'Community GGUF',
      count: null,
      image: '/huggingface-logo.svg',
      tone: 'amber',
    },
  ]
  const activeStyles = {
    emerald: {
      borderColor: 'rgba(52, 211, 153, 0.48)',
      background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(76, 29, 149, 0.14))',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08), 0 0 28px rgba(16,185,129,0.1)',
    },
    purple: {
      borderColor: 'rgba(184, 100, 255, 0.58)',
      background: 'linear-gradient(135deg, rgba(126, 34, 206, 0.22), rgba(71, 25, 120, 0.16))',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08), 0 0 30px rgba(157,0,255,0.15)',
    },
    amber: {
      borderColor: 'rgba(251, 191, 106, 0.72)',
      background: 'linear-gradient(135deg, rgba(120, 53, 15, 0.2), rgba(126, 34, 206, 0.22))',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.1), 0 0 32px rgba(251,191,36,0.14)',
    },
  }
  const iconStyles = {
    emerald: 'border-emerald-300/20 bg-emerald-400/10 text-emerald-200',
    purple: 'border-theme-accent/25 bg-theme-accent/10 text-theme-accent-light',
    amber: 'border-amber-300/20 bg-amber-300/10 text-amber-100',
  }
  const indicatorStyles = {
    emerald: 'bg-emerald-300 shadow-[0_0_10px_rgba(110,231,183,0.75)]',
    purple: 'bg-theme-accent-light shadow-[0_0_10px_rgba(192,132,252,0.8)]',
    amber: 'bg-amber-200 shadow-[0_0_12px_rgba(253,230,138,0.9)]',
  }
  return (
    <div
      className="mb-5 grid gap-2.5 rounded-lg border p-2.5 sm:grid-cols-3 sm:p-3"
      style={{
        background: `
          linear-gradient(135deg, rgba(12,10,22,0.96), rgba(25,12,38,0.9)),
          repeating-linear-gradient(90deg, transparent 0 35px, rgba(255,255,255,0.022) 35px 36px),
          repeating-linear-gradient(180deg, transparent 0 35px, rgba(255,255,255,0.02) 35px 36px)
        `,
        borderColor: 'rgba(190, 150, 255, 0.22)',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06), 0 18px 50px rgba(0,0,0,0.22)',
      }}
      role="tablist"
      aria-label="Model sources"
    >
      {tabs.map(({ id, label, detail, count, icon: Icon, image, tone }) => {
        const active = value === id
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(id)}
            style={active ? activeStyles[tone] : undefined}
            className={`group relative flex min-h-[86px] items-center gap-3 overflow-hidden rounded-lg border px-3.5 text-left transition-[border-color,background-color,box-shadow,transform] duration-200 sm:px-4 ${active ? 'text-theme-text' : 'border-white/[0.075] bg-black/20 text-theme-text-muted hover:-translate-y-px hover:border-white/[0.16] hover:bg-white/[0.035] hover:text-theme-text'}`}
          >
            <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] ${iconStyles[tone]}`}>
              {image
                ? <img src={image} alt="" className={image === '/osmantic-os-icon-192.png' ? 'h-10 w-10 object-contain' : 'h-8 w-8 object-contain'} />
                : <Icon size={27} strokeWidth={1.75} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold leading-4 text-theme-text sm:text-[15px] sm:leading-5">{label}</span>
              <span className={`mt-1 block truncate text-xs ${active && tone === 'amber' ? 'text-amber-200/80' : 'text-theme-text-muted/70'}`}>{detail}</span>
            </span>
            {count !== null && (
              <span className="flex h-10 min-w-10 shrink-0 items-center justify-end border-l border-white/[0.07] pl-3 font-mono text-lg font-semibold text-theme-accent-light">
                {count}
              </span>
            )}
            {active && <span className={`absolute bottom-0 left-1/2 h-px w-10 -translate-x-1/2 ${indicatorStyles[tone]}`} />}
          </button>
        )
      })}
    </div>
  )
}

function ModelsFilterPanel({
  query,
  setQuery,
  categoryFilter,
  setCategoryFilter,
  categoryOptions,
  compatibilityFilter,
  setCompatibilityFilter,
  speedFilter,
  setSpeedFilter,
  contextFloor,
  setContextFloor,
  maxContext,
  insights,
  onReset,
}) {
  return (
    <aside className="space-y-4">
      <section className="rounded-xl border p-4" style={TECH_PANEL_STYLE}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-bold text-theme-text">Filters</h2>
          <button
            type="button"
            onClick={onReset}
            className="text-[10px] font-semibold text-theme-text-muted transition-colors hover:text-theme-accent-light"
          >
            Reset
          </button>
        </div>

        <label className="relative block">
          <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-theme-text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search models..."
            className="h-9 w-full rounded-lg border border-white/[0.08] bg-black/25 pl-9 pr-3 text-xs text-theme-text outline-none transition-colors placeholder:text-theme-text-muted/60 focus:border-theme-accent/45"
          />
        </label>

        <div className="mt-5">
          <SectionLabel>Categories</SectionLabel>
          <div className="mt-2 space-y-1">
            {categoryOptions.map(option => (
              <button
                key={option.id}
                type="button"
                data-testid={`model-category-${option.id}`}
                onClick={() => setCategoryFilter(option.id)}
                className={`flex w-full items-center justify-between rounded-md px-2.5 py-2 text-left text-xs transition-colors ${
                  categoryFilter === option.id
                    ? 'bg-theme-accent text-white shadow-[0_0_18px_rgba(168,85,247,0.26)]'
                    : 'text-theme-text-secondary hover:bg-white/[0.045] hover:text-theme-text'
                }`}
              >
                <span>{option.label}</span>
                <span className={categoryFilter === option.id ? 'text-white' : 'text-theme-accent-light'}>
                  {option.count}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5">
          <SectionLabel>Compatibility</SectionLabel>
          <div className="mt-2 grid grid-cols-2 gap-1 overflow-hidden rounded-lg border border-white/[0.06] bg-black/20 p-1">
            {[
              ['all', 'All'],
              ['fits', 'Fits GPU'],
              ['balanced', 'Balanced'],
              ['high', 'High VRAM'],
            ].map(([id, label]) => (
              <FilterChip
                key={id}
                active={compatibilityFilter === id}
                onClick={() => setCompatibilityFilter(id)}
              >
                {label}
              </FilterChip>
            ))}
          </div>
        </div>

        <div className="mt-5">
          <SectionLabel>Context Length</SectionLabel>
          <input
            type="range"
            min="0"
            max={Math.max(0, maxContext)}
            step="8192"
            value={Math.min(contextFloor, maxContext)}
            onChange={(event) => setContextFloor(Number(event.target.value))}
            className="mt-3 h-1 w-full accent-theme-accent"
          />
          <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-theme-text-muted">
            <span>{contextFloor > 0 ? formatContext(contextFloor) : 'Any'}</span>
            <span>{maxContext > 0 ? formatContext(maxContext) : '--'}</span>
          </div>
        </div>

        <div className="mt-5">
          <SectionLabel>Speed Preference</SectionLabel>
          <div className="mt-2 grid grid-cols-4 gap-1 overflow-hidden rounded-lg border border-white/[0.06] bg-black/20 p-1">
            {[
              ['any', 'Any'],
              ['fast', 'Fast'],
              ['balanced', 'Balanced'],
              ['quality', 'Quality'],
            ].map(([id, label]) => (
              <FilterChip
                key={id}
                active={speedFilter === id}
                onClick={() => setSpeedFilter(id)}
              >
                {label}
              </FilterChip>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-xl border p-4" style={TECH_PANEL_STYLE}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-bold text-theme-text">Insights</h2>
          <span className="flex items-center gap-1.5 text-[10px] text-theme-text-muted">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Catalog summary
          </span>
        </div>
        <div className="space-y-2">
          {insights.map(item => (
            <div
              key={item.label}
              className="flex items-center justify-between rounded-lg border border-white/[0.055] bg-black/18 px-3 py-2 text-xs"
            >
              <span className="text-theme-text-muted">{item.label}</span>
              <span className="font-mono font-semibold text-theme-accent-light">{item.value}</span>
            </div>
          ))}
        </div>
      </section>
    </aside>
  )
}

function SectionLabel({ children }) {
  return <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-theme-text-muted/70">{children}</div>
}

function FilterChip({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md px-2 py-1.5 text-[10px] font-semibold transition-colors ${
        active
          ? 'bg-theme-accent text-white'
          : 'text-theme-text-muted hover:bg-white/[0.045] hover:text-theme-text'
      }`}
    >
      {children}
    </button>
  )
}

function ModelTableRow({
  model,
  gpu,
  canActivateModels,
  activationModeError,
  hermesMinimumContext,
  isCurrentModel,
  isLoading,
  loadBusy,
  activationBusy,
  downloadBusy,
  downloadStarting,
  onDownload,
  onLoad,
  onBenchmark,
  onDelete,
}) {
  const isLoaded = model.status === 'loaded' || isCurrentModel
  const isDownloaded = model.status === 'downloaded'
  const memory = getMemoryMeta(model, gpu)
  const compatibility = getCompatibilityMeta(model, memory, hermesMinimumContext)
  const speed = getSpeedDisplay(model)
  const tags = getModelTags(model, hermesMinimumContext)
  const iconTone = getIconTone(model, compatibility)
  const performanceBadge = getPerformanceBadge(model)
  const runDisabledReason = getRunDisabledReason({
    model,
    gpu,
    canActivateModels,
    activationModeError,
    hermesMinimumContext,
    loadBusy,
    activationBusy,
  })

  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-4 px-3 py-4 transition-colors hover:bg-white/[0.025] sm:grid-cols-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(250px,1.7fr)_144px_70px_110px_120px_90px_130px] lg:gap-5 lg:px-5 lg:py-3.5">
      <div className="col-span-2 min-w-0 sm:col-span-1 lg:col-span-1">
        <div className="flex min-w-0 items-start gap-3">
          <ModelPublisherIcon model={model} tone={iconTone} />
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h3 className="truncate text-sm font-semibold text-theme-text">{model.name}</h3>
              {model.quantization && <Badge>{model.quantization}</Badge>}
            </div>
            <p className="mt-1 truncate text-[11px] text-theme-text-muted/75">{model.description}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {tags.map(tag => <Badge key={tag} subdued>{tag}</Badge>)}
              {performanceBadge && <Badge tone={performanceBadge.tone}>{performanceBadge.label}</Badge>}
              {model.recommended && !isLoaded && <Badge tone="amber">Selected install</Badge>}
              {isLoaded && <Badge tone="green">Active</Badge>}
            </div>
          </div>
        </div>
      </div>

      <div className="col-span-2 flex items-center gap-2 self-start sm:col-span-1 sm:justify-end lg:col-span-1 lg:self-center lg:justify-start">
        <PrimaryAction
          model={model}
          isLoaded={isLoaded}
          isDownloaded={isDownloaded}
          isLoading={isLoading}
          activationBusy={activationBusy}
          downloadBusy={downloadBusy}
          downloadStarting={downloadStarting}
          runDisabledReason={runDisabledReason}
          hermesMinimumContext={hermesMinimumContext}
          onDownload={onDownload}
          onLoad={onLoad}
          onBenchmark={onBenchmark}
        />
        <DeleteAction
          model={model}
          isLoaded={isLoaded}
          isDownloaded={isDownloaded}
          isLoading={isLoading}
          activationBusy={activationBusy}
          onDelete={onDelete}
        />
      </div>

      <div className="self-center font-mono text-xs text-theme-text-secondary">
        <MobileMetricLabel>Size</MobileMetricLabel>
        {model.size || '--'}
      </div>

      <div className="self-center">
        <MobileMetricLabel>VRAM</MobileMetricLabel>
        <div className="mb-2 flex items-center justify-between gap-2 font-mono text-xs text-theme-text-secondary">
          <span>{memory.value}</span>
          <span className="text-[10px] text-theme-text-muted">{memory.percentLabel}</span>
        </div>
        <div className="liquid-metal-progress-track h-1.5 overflow-hidden rounded-full">
          <div
            className={`h-full rounded-full transition-all ${memory.tone}`}
            style={{ width: `${memory.barPercent}%` }}
          />
        </div>
      </div>

      <div className="self-center">
        <MobileMetricLabel>Speed</MobileMetricLabel>
        <div className="mb-1.5 font-mono text-xs text-theme-text-secondary">{speed.label}</div>
        <ModelSpeedVisual model={model} speed={speed} />
      </div>

      <div className="self-center font-mono text-xs text-theme-text-secondary">
        <MobileMetricLabel>Context</MobileMetricLabel>
        {formatContext(model.contextLength)}
      </div>

      <div className="col-span-2 self-center lg:col-span-1">
        <MobileMetricLabel>Compatibility</MobileMetricLabel>
        <Badge tone={compatibility.tone}>{compatibility.label}</Badge>
        <p className="mt-1 text-[10px] text-theme-text-muted">{compatibility.detail}</p>
      </div>
    </div>
  )
}

function PrimaryAction({
  model,
  isLoaded,
  isDownloaded,
  isLoading,
  activationBusy,
  downloadBusy,
  downloadStarting,
  runDisabledReason,
  hermesMinimumContext,
  onDownload,
  onLoad,
  onBenchmark,
}) {
  if (isLoading) {
    return (
      <button disabled className="inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md bg-theme-accent/20 px-3 text-xs font-semibold text-theme-accent">
        <Loader2 size={14} className="animate-spin" />
        Working
      </button>
    )
  }

  if (isLoaded) {
    return (
      <button
        type="button"
        onClick={onBenchmark}
        disabled={activationBusy}
        className="inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md bg-theme-accent px-3 text-xs font-semibold text-white shadow-[0_0_18px_rgba(168,85,247,0.26)] transition-colors hover:bg-theme-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
        title="Run a local benchmark for this loaded model"
      >
        <RefreshCw size={13} />
        Benchmark
      </button>
    )
  }

  if (isDownloaded) {
    const runDisabled = Boolean(runDisabledReason)
    const directChatBlocked = isOpenAiChatBlocked(getOpenAiChatCompatibility(model))
    const buttonLabel = directChatBlocked ? 'Chat Unsupported' : 'Run'
    return (
      <span className="inline-flex" title={runDisabledReason || `Run ${model.name}`}>
        <button
          type="button"
          onClick={onLoad}
          disabled={runDisabled}
          title={runDisabledReason || `Run ${model.name}`}
          className={`inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md px-3 text-xs font-semibold transition-colors ${
            !runDisabled
              ? 'bg-theme-accent text-white shadow-[0_0_18px_rgba(168,85,247,0.32)] hover:bg-theme-accent-hover'
              : 'cursor-not-allowed border border-white/[0.08] bg-black/20 text-theme-text-muted'
          }`}
        >
          {directChatBlocked ? <AlertCircle size={13} /> : <Play size={13} />}
          {buttonLabel}
        </button>
      </span>
    )
  }

  if (downloadStarting) {
    return (
      <button disabled className="inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md bg-theme-accent/20 px-3 text-xs font-semibold text-theme-accent">
        <Loader2 size={14} className="animate-spin" />
        Starting
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={onDownload}
      disabled={downloadBusy || activationBusy}
      className={`inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md border px-3 text-xs font-semibold transition-colors ${
        downloadBusy || activationBusy
          ? 'cursor-not-allowed border-white/[0.08] bg-black/20 text-theme-text-muted'
          : 'border-white/[0.08] bg-black/20 text-theme-text-secondary hover:border-theme-accent/35 hover:text-theme-text'
      }`}
    >
      <Download size={13} />
      Download
    </button>
  )
}

function DeleteAction({ model, isLoaded, isDownloaded, isLoading, activationBusy, onDelete }) {
  if (!isLoaded && !isDownloaded) return null

  const disabledReason = isLoaded
    ? 'The active model cannot be deleted. Run another model first.'
    : isLoading
      ? 'Wait for the current model action to finish before deleting it.'
      : activationBusy
        ? 'Wait for the current model swap to finish before deleting another model.'
        : null
  const title = disabledReason || `Delete ${model.name} from this device`

  return (
    <span className="inline-flex" title={title}>
      <button
        type="button"
        onClick={onDelete}
        disabled={Boolean(disabledReason)}
        aria-label={isLoaded ? `Delete ${model.name} unavailable` : `Delete ${model.name}`}
        title={title}
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition-colors ${
          disabledReason
            ? 'cursor-not-allowed border-white/[0.07] bg-black/20 text-theme-text-muted/45'
            : 'border-red-400/20 bg-red-500/[0.07] text-red-300 hover:border-red-300/40 hover:bg-red-500/15'
        }`}
      >
        <Trash2 size={14} />
      </button>
    </span>
  )
}

function DeleteModelDialog({ model, onCancel, onConfirm }) {
  const titleId = `delete-model-${model.id || 'model'}`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-xl border border-red-300/20 bg-[#08080d] p-5 shadow-[0_24px_80px_rgba(0,0,0,0.45)]"
      >
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-red-300/20 bg-red-500/10 text-red-300">
            <Trash2 size={17} />
          </div>
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-semibold text-theme-text">
              Delete {model.name}?
            </h2>
            <p className="mt-2 text-sm leading-5 text-theme-text-muted">
              The model file will be removed from this device. Download it again from the library if you need it later.
            </p>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex h-9 items-center justify-center rounded-md border border-white/[0.08] bg-black/20 px-3 text-xs font-semibold text-theme-text-secondary transition-colors hover:border-theme-accent/35 hover:text-theme-text"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="inline-flex h-9 items-center justify-center rounded-md border border-red-300/30 bg-red-500/15 px-3 text-xs font-semibold text-red-200 transition-colors hover:border-red-200/50 hover:bg-red-500/25"
          >
            Delete model
          </button>
        </div>
      </div>
    </div>
  )
}

function MobileMetricLabel({ children }) {
  return <span className="mb-1.5 block text-[9px] font-semibold uppercase text-theme-text-muted/60 lg:hidden">{children}</span>
}

function DownloadProgressBar({ progress, helpers, onRetry }) {
  const { formatBytes, formatEta, cancelDownload, cancelError, isCancelling } = helpers

  if (progress.error) {
    const cancelled = progress.status === 'cancelled'
    return (
      <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <AlertCircle size={20} className="shrink-0 text-red-400" />
            <div className="min-w-0">
              <p className="font-medium text-red-300">{cancelled ? 'Download Cancelled' : 'Download Failed'}</p>
              <p className="break-words text-sm text-red-300/70">{progress.error}</p>
            </div>
          </div>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded-md border border-red-400/25 bg-black/20 px-3 text-xs font-semibold text-red-200 transition-colors hover:border-red-300/45 hover:bg-red-500/10"
            >
              <RefreshCw size={13} />
              Retry
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="mb-5 rounded-xl border border-theme-accent/30 bg-theme-accent/10 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="relative shrink-0">
            <HardDrive size={20} className="text-theme-accent" />
            <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-theme-accent" />
          </div>
          <div className="min-w-0">
            <p className="truncate font-medium text-theme-text">
              {progress.status === 'verifying' ? 'Verifying' : 'Downloading'} {progress.model}
            </p>
            <p className="text-sm text-theme-text-muted">
              {formatBytes(progress.bytesDownloaded)} / {formatBytes(progress.bytesTotal)}
              {progress.speedMbps > 0 && ` - ${progress.speedMbps.toFixed(1)} MB/s`}
              {progress.eta && ` - ETA: ${formatEta(progress.eta)}`}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-lg font-bold text-theme-accent">
            {progress.percent?.toFixed(0) || 0}%
          </span>
          <button
            type="button"
            onClick={cancelDownload}
            disabled={isCancelling}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-white/[0.1] bg-black/20 px-2.5 text-xs font-semibold text-theme-text-secondary transition-colors hover:border-red-400/35 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isCancelling ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />}
            {isCancelling ? 'Cancelling' : 'Cancel'}
          </button>
        </div>
      </div>

      {cancelError && (
        <p role="alert" className="mb-3 text-sm text-red-300">
          {cancelError}
        </p>
      )}

      <div className="h-2.5 overflow-hidden rounded-full bg-theme-border">
        <div
          className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-300"
          style={{ width: `${progress.percent || 0}%` }}
        />
      </div>
    </div>
  )
}

function Pagination({ page, pageCount, onChange }) {
  const pages = buildPageList(page, pageCount)
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onChange(Math.max(1, page - 1))}
        disabled={page <= 1}
        className="flex h-7 w-7 items-center justify-center rounded-md border border-white/[0.06] text-theme-text-muted transition-colors hover:text-theme-text disabled:opacity-35"
        title="Previous page"
      >
        <ChevronLeft size={14} />
      </button>
      {pages.map((item, index) => item === 'gap' ? (
        <span key={`gap-${index}`} className="px-1 text-theme-text-muted/60">...</span>
      ) : (
        <button
          key={item}
          type="button"
          onClick={() => onChange(item)}
          className={`h-7 min-w-7 rounded-md border px-2 text-xs font-semibold transition-colors ${
            item === page
              ? 'border-theme-accent/40 bg-theme-accent text-white'
              : 'border-white/[0.06] text-theme-text-muted hover:text-theme-text'
          }`}
          aria-current={item === page ? 'page' : undefined}
        >
          {item}
        </button>
      ))}
      <button
        type="button"
        onClick={() => onChange(Math.min(pageCount, page + 1))}
        disabled={page >= pageCount}
        className="flex h-7 w-7 items-center justify-center rounded-md border border-white/[0.06] text-theme-text-muted transition-colors hover:text-theme-text disabled:opacity-35"
        title="Next page"
      >
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

function getRunDisabledReason({
  model,
  gpu,
  canActivateModels,
  activationModeError,
  hermesMinimumContext,
  loadBusy,
  activationBusy,
}) {
  if (!canActivateModels) {
    return activationModeError || 'The local model runtime is unavailable. Review runtime settings before running this model.'
  }
  const openAiChat = getOpenAiChatCompatibility(model)
  if (isOpenAiChatBlocked(openAiChat)) {
    return openAiChat.reason || 'This model is not currently validated for direct local chat.'
  }
  if (model.fitsVram !== true && !model.recommended) {
    const required = Number(model.estimatedRequired || model.vramRequired || 0)
    const total = Number(gpu?.vramTotal || 0)
    if (required > 0 && total > 0) {
      return `Requires ${formatNumber(required)} GB VRAM; the detected GPU has ${formatNumber(total)} GB total.`
    }
    return 'This model does not fit the detected GPU memory.'
  }
  if (activationBusy) return 'Wait for the current model swap to finish.'
  if (loadBusy) return 'Another model action is in progress.'
  return null
}

function formatModeLabel(mode) {
  if (!mode || mode === 'unknown') return 'Unknown'
  if (mode === 'lemonade') return 'Lemonade'
  return `${mode.charAt(0).toUpperCase()}${mode.slice(1)}`
}

function ModelSpeedVisual({ model, speed, compact = false }) {
  const points = buildSpeedProfilePoints(model, speed.value)
  const fillId = `speed-${compact ? 'hero' : 'row'}-${model?.id || 'unknown'}-fill`.replace(/[^a-zA-Z0-9_-]/g, '-')
  const sizeClass = compact ? 'h-11 w-52' : 'h-7 w-24'

  if (!points.length) {
    return (
      <div className={`${compact ? 'h-11 w-52' : 'h-7 w-24'} rounded bg-white/[0.025]`}>
        <div className="mx-2 h-full border-b border-dashed border-white/[0.08]" />
      </div>
    )
  }

  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')
  const area = `${path} L 100 30 L 0 30 Z`
  const stroke = speed.tone === 'orange' ? '#f59e0b' : '#a855f7'

  return (
    <svg viewBox="0 0 100 30" className={sizeClass} aria-hidden="true">
      <defs>
        <linearGradient id={fillId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${fillId})`} />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Badge({ children, tone = 'neutral', subdued = false }) {
  const classes = {
    neutral: subdued
      ? 'border-white/[0.08] bg-white/[0.035] text-theme-text-muted'
      : 'border-white/[0.08] bg-white/[0.055] text-theme-text-secondary',
    green: 'border-emerald-400/20 bg-emerald-500/12 text-emerald-300',
    amber: 'border-amber-400/25 bg-amber-500/12 text-amber-300',
    red: 'border-red-400/25 bg-red-500/12 text-red-300',
    purple: 'border-theme-accent/25 bg-theme-accent/12 text-theme-accent-light',
  }
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold leading-none ${classes[tone] || classes.neutral}`}>
      {children}
    </span>
  )
}

function buildCategoryOptions(models, hermesMinimumContext) {
  const definitions = [
    { id: 'chat', label: 'Chat / LLM' },
    { id: 'code', label: 'Code' },
    { id: 'reasoning', label: 'Reasoning' },
    { id: 'long-context', label: 'Long Context' },
    { id: 'moe', label: 'MoE' },
    { id: 'other', label: 'Other' },
  ]
  const counts = Object.fromEntries(definitions.map(definition => [definition.id, 0]))
  models.forEach(model => {
    getModelCategoryIds(model, hermesMinimumContext).forEach(category => {
      counts[category] = (counts[category] || 0) + 1
    })
  })
  return [
    { id: 'all', label: 'All Models', count: models.length },
    ...definitions
      .map(definition => ({ ...definition, count: counts[definition.id] || 0 }))
      .filter(definition => definition.count > 0),
  ]
}

function getModelCategoryIds(model, hermesMinimumContext) {
  const categories = new Set()
  const text = [
    model?.id,
    model?.name,
    model?.specialty,
    model?.description,
    model?.architecture,
    model?.llmModelName,
  ].filter(Boolean).join(' ').toLowerCase()

  categories.add('chat')
  if (text.includes('code') || text.includes('coder')) categories.add('code')
  if (
    text.includes('reason') ||
    text.includes('deepseek') ||
    text.includes('math') ||
    text.includes('stem')
  ) {
    categories.add('reasoning')
  }
  const minimumContext = Number(hermesMinimumContext || 0)
  if ((minimumContext > 0 && Number(model?.contextLength || 0) >= minimumContext) || text.includes('long context')) {
    categories.add('long-context')
  }
  if (
    text.includes('moe') ||
    /\b[ae]\d+b\b/i.test(text) ||
    Number(model?.metadata?.expertCount || 0) > 0
  ) {
    categories.add('moe')
  }
  if (categories.size === 0) categories.add('other')
  return [...categories]
}

function matchesModelSearch(model, search) {
  return [
    model?.id,
    model?.name,
    model?.gguf,
    model?.quantization,
    model?.specialty,
    model?.description,
    model?.llmModelName,
  ].filter(Boolean).join(' ').toLowerCase().includes(search)
}

function matchesCompatibilityFilter(model, memory, filter) {
  if (filter === 'all') return true
  if (filter === 'fits') return !!model?.fitsVram
  if (filter === 'balanced') return !!model?.fitsVram && memory.percent > 0 && memory.percent <= 82
  if (filter === 'high') {
    if (model?.fitsVram && memory.percent > 82) return true
    return !model?.fitsVram && memory.total > 0 && memory.required <= memory.total * 1.08
  }
  return true
}

function matchesSpeedFilter(model, filter, hermesMinimumContext) {
  if (filter === 'any') return true
  const speed = getSpeedDisplay(model).value || 0
  if (filter === 'fast') return speed >= 45
  if (filter === 'balanced') return speed >= 15 && speed < 45
  if (filter === 'quality') {
    const text = `${model?.name || ''} ${model?.specialty || ''} ${model?.description || ''}`.toLowerCase()
    const minimumContext = Number(hermesMinimumContext || 0)
    return text.includes('quality') ||
      text.includes('flagship') ||
      text.includes('top-tier') ||
      (minimumContext > 0 && Number(model?.contextLength || 0) >= minimumContext)
  }
  return true
}

function buildModelInsights(models) {
  const installedModels = models.filter(model => ['downloaded', 'loaded'].includes(model.status))
  const installedSize = installedModels.reduce((total, model) => total + Number(model.sizeGb || 0), 0)
  const catalogSize = models.reduce((total, model) => total + Number(model.sizeGb || 0), 0)
  return [
    {
      label: 'Models That Fit Your GPU',
      value: models.filter(model => model.fitsVram).length,
    },
    {
      label: 'Installed Models',
      value: installedModels.length,
    },
    {
      label: 'Available Models',
      value: models.filter(model => model.status === 'available').length,
    },
    {
      label: 'Installed Storage',
      value: installedSize > 0 ? `${formatNumber(installedSize)} GB` : '0 GB',
    },
    {
      label: 'Catalog Size',
      value: catalogSize > 0 ? `${formatNumber(catalogSize)} GB` : '0 GB',
    },
  ]
}

function getMemoryMeta(model, gpu) {
  const estimated = Number(model?.estimatedRequired || 0)
  const catalog = Number(model?.vramRequired || 0)
  const required = estimated > catalog + 0.1 ? estimated : catalog
  const includesKv = estimated > catalog + 0.1
  const total = Number(gpu?.vramTotal || 0)
  const percent = total > 0 && required > 0 ? Math.round((required / total) * 100) : 0
  const barPercent = total > 0 && required > 0 ? Math.min(100, Math.max(3, percent)) : 0
  return {
    value: required > 0 ? `${includesKv ? '~' : ''}${formatNumber(required)} GB${includesKv ? ' incl. KV' : ''}` : '--',
    label: required > 0 ? `${formatNumber(required)} / ${formatNumber(total || 0)} GB` : '--',
    percent,
    percentLabel: total > 0 && required > 0 ? `${percent}%` : '--',
    barPercent,
    required,
    total,
    tone: percent > 90
      ? 'liquid-metal-progress-fill liquid-metal-progress-fill--danger'
      : percent > 70
        ? 'liquid-metal-progress-fill liquid-metal-progress-fill--warn'
        : 'liquid-metal-progress-fill',
  }
}

function getCompatibilityMeta(model, memory, hermesMinimumContext = 0) {
  if (!model?.fitsVram) {
    const nearLimit = memory.total > 0 && memory.required <= memory.total * 1.08
    return {
      label: nearLimit ? 'High VRAM' : 'Too large',
      detail: nearLimit ? 'Heavy' : 'Incompatible',
      tone: nearLimit ? 'amber' : 'red',
    }
  }
  const openAiChat = getOpenAiChatCompatibility(model)
  if (isOpenAiChatBlocked(openAiChat)) {
    return {
      label: 'Unavailable',
      detail: 'Chat blocked',
      tone: 'red',
    }
  }
  const agentViability = getAgentViabilityCompatibility(model)
  if (isAgentViabilityBlocked(agentViability)) {
    return {
      label: 'Direct chat only',
      detail: 'Agent blocked',
      tone: 'amber',
    }
  }
  const appBlock = getBlockedAppCompatibility(model)
  if (appBlock) {
    return {
      label: 'App limited',
      detail: `${formatCompatibilityAppName(appBlock.key)} blocked`,
      tone: 'amber',
    }
  }
  const contextLength = Number(model?.contextLength || 0)
  const minimumContext = Number(hermesMinimumContext || 0)
  if (minimumContext > 0 && contextLength > 0 && contextLength < minimumContext) {
    return {
      label: 'Direct chat only',
      detail: `Needs ${formatContext(minimumContext)}`,
      tone: 'amber',
    }
  }
  const talkCompatibility = getHermesTalkCompatibility(model)
  if (isHermesTalkVerified(talkCompatibility)) {
    return { label: 'Talk ready', detail: model.recommended || model.status === 'loaded' ? 'Best' : 'Verified', tone: 'green' }
  }
  if (model.recommended || model.status === 'loaded') {
    return { label: model.fitLabel || 'Fits GPU', detail: 'Best', tone: 'green' }
  }
  if (memory.percent > 82) return { label: model.fitLabel || 'Fits GPU', detail: 'Good', tone: 'green' }
  return { label: model.fitLabel || 'Fits GPU', detail: memory.percent < 45 ? 'Excellent' : 'Good', tone: 'green' }
}

function getHermesTalkCompatibility(model) {
  return model?.appCompatibility?.hermesTalk || null
}

function getOpenAiChatCompatibility(model) {
  return model?.appCompatibility?.openaiChat || null
}

function getAgentViabilityCompatibility(model) {
  return model?.appCompatibility?.agentViability || getHermesTalkCompatibility(model)
}

function getBlockedAppCompatibility(model) {
  const compatibility = model?.appCompatibility || {}
  for (const [key, entry] of Object.entries(compatibility)) {
    if (CORE_MODEL_COMPATIBILITY_KEYS.has(key)) continue
    if (isAgentViabilityBlocked(entry) || isOpenAiChatBlocked(entry)) return { key, entry }
  }
  return null
}

function formatCompatibilityAppName(key) {
  const known = {
    litellm: 'LiteLLM',
    openWebui: 'Open WebUI',
    opencode: 'OpenCode',
    openclaw: 'OpenClaw',
    perplexica: 'Perplexica',
    privacyShield: 'Privacy Shield',
    tokenSpy: 'Token Spy',
  }
  if (known[key]) return known[key]
  return String(key || 'App')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/^./, value => value.toUpperCase())
}

function isOpenAiChatBlocked(compatibility) {
  const status = String(compatibility?.status || '').toLowerCase()
  return BLOCKING_MODEL_COMPATIBILITY_STATUSES.includes(status)
}

function isAgentViabilityBlocked(compatibility) {
  const status = String(compatibility?.status || '').toLowerCase()
  return ['not_agent_viable', ...BLOCKING_MODEL_COMPATIBILITY_STATUSES].includes(status)
}

const BLOCKING_MODEL_COMPATIBILITY_STATUSES = [
  'blocked',
  'incompatible',
  'not_recommended',
  'not_supported',
  'unsupported',
  'unsupported_until_revalidated',
]

const CORE_MODEL_COMPATIBILITY_KEYS = new Set([
  'agentViability',
  'hermesTalk',
  'openaiChat',
])

function isHermesTalkBlocked(compatibility) {
  const status = String(compatibility?.status || '').toLowerCase()
  return BLOCKING_MODEL_COMPATIBILITY_STATUSES.includes(status)
}

function isHermesTalkVerified(compatibility) {
  const status = String(compatibility?.status || '').toLowerCase()
  return ['supported', 'verified'].includes(status)
}

function getSpeedDisplay(model) {
  const rawValue = toNumber(model?.tokensPerSec) || extractTokensPerSecond(model?.performanceLabel)
  const value = rawValue && rawValue <= MAX_SINGLE_REQUEST_TOKENS_PER_SECOND ? rawValue : null
  return {
    value,
    label: value ? (model?.performanceLabel || `${formatNumber(value)} tok/s`) : 'Benchmark required',
    tone: model?.fitsVram === false ? 'orange' : 'purple',
  }
}

function getPerformanceBadge(model) {
  const badges = {
    measured_local: { tone: 'green', label: 'Measured locally' },
    published_exact: { tone: 'purple', label: 'Published exact' },
    predicted_calibrated: { tone: 'purple', label: 'Calibrated estimate' },
    benchmark_required: { tone: 'amber', label: 'Benchmark required' },
    incompatible: { tone: 'red', label: 'Incompatible' },
  }
  return badges[model?.performance?.source] || null
}

function extractTokensPerSecond(label) {
  const match = String(label || '').match(/(\d+(?:\.\d+)?)\s*tok\/s/i)
  return match ? toNumber(match[1]) : null
}

function buildSpeedProfilePoints(model, speed) {
  if (!speed) return []
  const seed = hashString(`${model?.id || model?.name || 'model'}:${model?.contextLength || 0}`)
  const count = 14
  const base = Math.max(0.22, Math.min(0.78, speed / 140))
  const amplitude = 0.07 + (seed % 7) * 0.01
  const slope = ((Math.floor(seed / 7) % 5) - 2) * 0.012

  return Array.from({ length: count }, (_, index) => {
    const phase = ((seed % 11) / 10) + index * 0.82
    const wave = Math.sin(phase) * amplitude
    const secondary = Math.sin(phase * 1.9 + (seed % 5)) * 0.035
    const jitter = (((seed >> (index % 16)) & 3) - 1.5) * 0.018
    const ratio = clamp(base + wave + secondary + jitter + slope * index, 0.12, 0.92)
    return {
      x: (100 / (count - 1)) * index,
      y: 25 - ratio * 20,
    }
  })
}

function getModelTags(model, hermesMinimumContext) {
  const tags = []
  const name = `${model?.name || ''} ${model?.specialty || ''}`.toLowerCase()
  const add = (tag) => {
    if (tag && !tags.includes(tag)) tags.push(tag)
  }
  if (name.includes('code') || name.includes('coder')) add('Code')
  if (name.includes('reason') || name.includes('deepseek')) add('Reasoning')
  const minimumContext = Number(hermesMinimumContext || 0)
  if (minimumContext > 0 && (model?.contextLength || 0) >= minimumContext) add('Long Context')
  add(model?.specialty || 'General')
  add('Chat')
  return tags.slice(0, 3)
}

function getIconTone(model, compatibility) {
  if (!model?.fitsVram) return { border: 'border-orange-400/35', bg: 'bg-orange-500/10', text: 'text-orange-400' }
  if (compatibility.tone === 'amber') return { border: 'border-amber-400/35', bg: 'bg-amber-500/10', text: 'text-amber-300' }
  if (compatibility.detail === 'Best') return { border: 'border-theme-accent/35', bg: 'bg-theme-accent/10', text: 'text-theme-accent' }
  return { border: 'border-emerald-400/30', bg: 'bg-emerald-500/10', text: 'text-emerald-400' }
}

function ModelPublisherIcon({ model, tone, size = 'row' }) {
  const author = model?.publisher?.huggingFaceAuthor
  const publisherName = model?.publisher?.name || author
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => setImageFailed(false), [author])
  const large = size === 'large'
  const dimensions = large ? 'h-12 w-12 rounded-xl' : 'mt-0.5 h-7 w-7 rounded-lg'
  const fallbackTone = tone || {
    border: 'border-theme-accent/25',
    bg: 'bg-theme-accent/10',
    text: 'text-theme-accent',
  }

  return (
    <div className={`relative flex shrink-0 items-center justify-center overflow-hidden border ${dimensions} ${fallbackTone.border} ${fallbackTone.bg}`}>
      <Box size={large ? 25 : 17} className={fallbackTone.text} />
      {author && !imageFailed && (
        <img
          src={`/api/models/huggingface/authors/${encodeURIComponent(author)}/avatar`}
          alt={`${publisherName} logo`}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setImageFailed(true)}
          className="absolute inset-0 h-full w-full bg-[#111118] object-cover"
        />
      )}
    </div>
  )
}

function buildPageList(page, pageCount) {
  if (pageCount <= 5) return Array.from({ length: pageCount }, (_, index) => index + 1)
  if (page <= 3) return [1, 2, 3, 'gap', pageCount]
  if (page >= pageCount - 2) return [1, 'gap', pageCount - 2, pageCount - 1, pageCount]
  return [1, 'gap', page, 'gap', pageCount]
}

function formatContext(contextLength) {
  const value = Number(contextLength || 0)
  if (!value) return '--'
  return `${Math.round(value / 1024)}K`
}

function formatNumber(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  if (numeric >= 10) return numeric.toFixed(1).replace(/\.0$/, '')
  return numeric.toFixed(1)
}

function hashString(value) {
  return String(value).split('').reduce((hash, char) => {
    return ((hash << 5) - hash + char.charCodeAt(0)) >>> 0
  }, 2166136261)
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null
}
