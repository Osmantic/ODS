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
  MoreVertical,
  Play,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react'
import { useModels } from '../hooks/useModels'
import { useDownloadProgress } from '../hooks/useDownloadProgress'

const PAGE_SIZE = 10
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

export default function Models() {
  const downloadProgress = useDownloadProgress()
  const {
    models,
    gpu,
    currentModel,
    loading,
    error,
    actionLoading,
    downloadModel,
    loadModel,
    deleteModel,
    refresh,
  } = useModels()

  const [downloadStarting, setDownloadStarting] = useState(null)
  const [openMenuId, setOpenMenuId] = useState(null)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [compatibilityFilter, setCompatibilityFilter] = useState('all')
  const [speedFilter, setSpeedFilter] = useState('any')
  const [contextFloor, setContextFloor] = useState(0)
  const libraryRef = useRef(null)

  useEffect(() => {
    if (downloadProgress.isDownloading || error) {
      setDownloadStarting(null)
    }
  }, [downloadProgress.isDownloading, error])

  useEffect(() => {
    if (downloadProgress.completedDownload?.status === 'complete') {
      setDownloadStarting(null)
      refresh()
    }
  }, [downloadProgress.completedDownload, refresh])

  useEffect(() => {
    const closeMenu = (event) => {
      if (!event.target.closest('[data-model-menu]')) setOpenMenuId(null)
    }
    const onEscape = (event) => {
      if (event.key === 'Escape') setOpenMenuId(null)
    }
    document.addEventListener('mousedown', closeMenu)
    document.addEventListener('keydown', onEscape)
    return () => {
      document.removeEventListener('mousedown', closeMenu)
      document.removeEventListener('keydown', onEscape)
    }
  }, [])

  useEffect(() => {
    setPage(1)
  }, [models.length])

  const activeModel = useMemo(() => {
    return models.find(model => model.status === 'loaded')
      || models.find(model => model.id === currentModel)
      || null
  }, [currentModel, models])

  const categoryOptions = useMemo(() => buildCategoryOptions(models), [models])
  const maxContext = useMemo(
    () => Math.max(0, ...models.map(model => Number(model.contextLength || 0))),
    [models]
  )
  const modelInsights = useMemo(
    () => buildModelInsights(models),
    [models]
  )
  const filteredModels = useMemo(() => {
    const search = query.trim().toLowerCase()
    return models.filter(model => {
      const memory = getMemoryMeta(model, gpu)
      if (search && !matchesModelSearch(model, search)) return false
      if (categoryFilter !== 'all' && !getModelCategoryIds(model).includes(categoryFilter)) return false
      if (!matchesCompatibilityFilter(model, memory, compatibilityFilter)) return false
      if (!matchesSpeedFilter(model, speedFilter)) return false
      if (contextFloor > 0 && Number(model.contextLength || 0) < contextFloor) return false
      return true
    })
  }, [categoryFilter, compatibilityFilter, contextFloor, gpu, models, query, speedFilter])

  const pageCount = Math.max(1, Math.ceil(filteredModels.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const visibleModels = filteredModels.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)
  const startIndex = filteredModels.length ? (safePage - 1) * PAGE_SIZE + 1 : 0
  const endIndex = Math.min(safePage * PAGE_SIZE, filteredModels.length)

  useEffect(() => {
    setPage(1)
  }, [categoryFilter, compatibilityFilter, contextFloor, models.length, query, speedFilter])

  const handleDownload = async (modelId) => {
    setDownloadStarting(modelId)
    await downloadModel(modelId)
    downloadProgress.refresh()
  }

  if (loading) {
    return (
      <div className="p-6 sm:p-8">
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
    <div className="p-6 sm:p-8">
      <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-theme-text">Models</h1>
          <p className="mt-1 text-sm text-theme-text-muted">
            Discover, filter, and deploy the right model for your workflow.
          </p>
        </div>

        <div className="flex items-center gap-2">
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

      {downloadProgress.isDownloading && downloadProgress.progress && (
        <DownloadProgressBar progress={downloadProgress.progress} helpers={downloadProgress} />
      )}

      <CurrentModelPanel
        model={activeModel}
        currentModel={currentModel}
        gpu={gpu}
      />

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
            <div className="min-w-[1020px]">
              <div className="grid grid-cols-[minmax(280px,1.7fr)_90px_130px_150px_110px_150px_130px_36px] gap-5 border-b border-white/[0.055] px-5 py-3 text-[9px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/55">
                <span>Model</span>
                <span>Size</span>
                <span>VRAM</span>
                <span>Speed</span>
                <span>Context</span>
                <span>Compatibility</span>
                <span>Action</span>
                <span />
              </div>

              <div className="divide-y divide-white/[0.04]">
                {visibleModels.map((model, index) => {
                  const rowId = `${model.id || model.name || 'model'}:${startIndex + index}`
                  return (
                    <ModelTableRow
                      key={rowId}
                      model={model}
                      gpu={gpu}
                      isLoading={actionLoading === model.id}
                      loadBusy={!!actionLoading}
                      downloadBusy={downloadProgress.isDownloading || !!downloadStarting}
                      downloadStarting={downloadStarting === model.id}
                      menuOpen={openMenuId === rowId}
                      onToggleMenu={() => setOpenMenuId(current => current === rowId ? null : rowId)}
                      onDownload={() => handleDownload(model.id)}
                      onLoad={() => loadModel(model.id)}
                      onDelete={() => deleteModel(model.id)}
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
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-theme-accent/25 bg-theme-accent/10">
            <Box size={25} className="text-theme-accent" />
          </div>
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

        <a
          href="/"
          className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-white/[0.08] bg-black/20 px-3 text-xs font-semibold text-theme-text transition-colors hover:border-theme-accent/35 hover:bg-theme-accent/10"
        >
          Dashboard
        </a>
      </div>
    </section>
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
  isLoading,
  loadBusy,
  downloadBusy,
  downloadStarting,
  menuOpen,
  onToggleMenu,
  onDownload,
  onLoad,
  onDelete,
}) {
  const isLoaded = model.status === 'loaded'
  const isDownloaded = model.status === 'downloaded'
  const memory = getMemoryMeta(model, gpu)
  const compatibility = getCompatibilityMeta(model, memory)
  const speed = getSpeedDisplay(model)
  const tags = getModelTags(model)
  const iconTone = getIconTone(model, compatibility)

  return (
    <div className="grid grid-cols-[minmax(280px,1.7fr)_90px_130px_150px_110px_150px_130px_36px] gap-5 px-5 py-3.5 transition-colors hover:bg-white/[0.025]">
      <div className="min-w-0">
        <div className="flex min-w-0 items-start gap-3">
          <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border ${iconTone.border} ${iconTone.bg}`}>
            <Box size={17} className={iconTone.text} />
          </div>
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h3 className="truncate text-sm font-semibold text-theme-text">{model.name}</h3>
              {model.quantization && <Badge>{model.quantization}</Badge>}
            </div>
            <p className="mt-1 truncate text-[11px] text-theme-text-muted/75">{model.description}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {tags.map(tag => <Badge key={tag} subdued>{tag}</Badge>)}
            </div>
          </div>
        </div>
      </div>

      <div className="self-center font-mono text-xs text-theme-text-secondary">{model.size || '--'}</div>

      <div className="self-center">
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
        <div className="mb-1.5 font-mono text-xs text-theme-text-secondary">{speed.label}</div>
        <ModelSpeedVisual model={model} speed={speed} />
      </div>

      <div className="self-center font-mono text-xs text-theme-text-secondary">{formatContext(model.contextLength)}</div>

      <div className="self-center">
        <Badge tone={compatibility.tone}>{compatibility.label}</Badge>
        <p className="mt-1 text-[10px] text-theme-text-muted">{compatibility.detail}</p>
      </div>

      <div className="self-center">
        <PrimaryAction
          model={model}
          isLoaded={isLoaded}
          isDownloaded={isDownloaded}
          isLoading={isLoading}
          loadBusy={loadBusy}
          downloadBusy={downloadBusy}
          downloadStarting={downloadStarting}
          onDownload={onDownload}
          onLoad={onLoad}
        />
      </div>

      <div className="relative flex items-center justify-end self-center" data-model-menu>
        <button
          type="button"
          onClick={onToggleMenu}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-theme-text-muted transition-colors hover:bg-white/[0.05] hover:text-theme-text"
          title="Model actions"
        >
          <MoreVertical size={15} />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-9 z-30 w-44 overflow-hidden rounded-lg border border-white/[0.08] bg-[#101018] py-1 shadow-2xl">
            {isDownloaded && !isLoaded && (
              <MenuButton onClick={onLoad} icon={Play} disabled={!model.fitsVram || loadBusy}>Run model</MenuButton>
            )}
            {model.status === 'available' && (
              <MenuButton onClick={onDownload} icon={Download} disabled={downloadBusy}>Download</MenuButton>
            )}
            {(isDownloaded || isLoaded) && (
              <MenuButton onClick={onDelete} icon={Trash2} danger>Delete file</MenuButton>
            )}
            {!isLoaded && !isDownloaded && model.status !== 'available' && (
              <div className="px-3 py-2 text-xs text-theme-text-muted">No local action</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function PrimaryAction({
  model,
  isLoaded,
  isDownloaded,
  isLoading,
  loadBusy,
  downloadBusy,
  downloadStarting,
  onDownload,
  onLoad,
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
      <span className="inline-flex h-8 min-w-24 items-center justify-center rounded-md border border-emerald-400/20 bg-emerald-500/10 px-3 text-xs font-semibold text-emerald-300">
        Active
      </span>
    )
  }

  if (isDownloaded) {
    return (
      <button
        type="button"
        onClick={onLoad}
        disabled={!model.fitsVram || loadBusy}
        className={`inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md px-3 text-xs font-semibold transition-colors ${
          model.fitsVram && !loadBusy
            ? 'bg-theme-accent text-white shadow-[0_0_18px_rgba(168,85,247,0.32)] hover:bg-theme-accent-hover'
            : 'cursor-not-allowed border border-white/[0.08] bg-black/20 text-theme-text-muted'
        }`}
      >
        <Play size={13} />
        Run
      </button>
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
      disabled={downloadBusy}
      className={`inline-flex h-8 min-w-24 items-center justify-center gap-2 rounded-md border px-3 text-xs font-semibold transition-colors ${
        downloadBusy
          ? 'cursor-not-allowed border-white/[0.08] bg-black/20 text-theme-text-muted'
          : 'border-white/[0.08] bg-black/20 text-theme-text-secondary hover:border-theme-accent/35 hover:text-theme-text'
      }`}
    >
      <Download size={13} />
      Download
    </button>
  )
}

function DownloadProgressBar({ progress, helpers }) {
  const { formatBytes, formatEta } = helpers

  if (progress.error) {
    return (
      <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4">
        <div className="flex items-center gap-3">
          <AlertCircle size={20} className="text-red-400" />
          <div>
            <p className="font-medium text-red-300">Download Failed</p>
            <p className="text-sm text-red-300/70">{progress.error}</p>
          </div>
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
        <span className="text-lg font-bold text-theme-accent">
          {progress.percent?.toFixed(0) || 0}%
        </span>
      </div>

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

function MenuButton({ icon: Icon, children, onClick, disabled, danger }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
        danger
          ? 'text-red-300 hover:bg-red-500/10'
          : 'text-theme-text-secondary hover:bg-white/[0.045] hover:text-theme-text'
      }`}
    >
      <Icon size={13} />
      {children}
    </button>
  )
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

function buildCategoryOptions(models) {
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
    getModelCategoryIds(model).forEach(category => {
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

function getModelCategoryIds(model) {
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
  if (Number(model?.contextLength || 0) >= 64000 || text.includes('long context')) {
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

function matchesSpeedFilter(model, filter) {
  if (filter === 'any') return true
  const speed = getSpeedDisplay(model).value || 0
  if (filter === 'fast') return speed >= 45
  if (filter === 'balanced') return speed >= 15 && speed < 45
  if (filter === 'quality') {
    const text = `${model?.name || ''} ${model?.specialty || ''} ${model?.description || ''}`.toLowerCase()
    return text.includes('quality') || text.includes('flagship') || text.includes('top-tier') || Number(model?.contextLength || 0) >= 64000
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
  const total = Number(gpu?.vramTotal || 0)
  const percent = total > 0 && required > 0 ? Math.round((required / total) * 100) : 0
  const barPercent = total > 0 && required > 0 ? Math.min(100, Math.max(3, percent)) : 0
  return {
    value: required > 0 ? `${formatNumber(required)} GB` : '--',
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

function getCompatibilityMeta(model, memory) {
  if (!model?.fitsVram) {
    const nearLimit = memory.total > 0 && memory.required <= memory.total * 1.08
    return {
      label: nearLimit ? 'High VRAM' : 'Too large',
      detail: nearLimit ? 'Heavy' : 'Incompatible',
      tone: nearLimit ? 'amber' : 'red',
    }
  }
  if (model.recommended || model.status === 'loaded') {
    return { label: model.fitLabel || 'Fits GPU', detail: 'Best', tone: 'green' }
  }
  if (memory.percent > 82) return { label: model.fitLabel || 'Fits GPU', detail: 'Good', tone: 'green' }
  return { label: model.fitLabel || 'Fits GPU', detail: memory.percent < 45 ? 'Excellent' : 'Good', tone: 'green' }
}

function getSpeedDisplay(model) {
  const value = toNumber(model?.tokensPerSec)
  return {
    value,
    label: value ? `${formatNumber(value)} tok/s` : '--',
    tone: model?.fitsVram === false ? 'orange' : 'purple',
  }
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

function getModelTags(model) {
  const tags = []
  const name = `${model?.name || ''} ${model?.specialty || ''}`.toLowerCase()
  const add = (tag) => {
    if (tag && !tags.includes(tag)) tags.push(tag)
  }
  if (name.includes('code') || name.includes('coder')) add('Code')
  if (name.includes('reason') || name.includes('deepseek')) add('Reasoning')
  if ((model?.contextLength || 0) >= 64000) add('Long Context')
  add(model?.specialty || 'General')
  add('Chat')
  return tags.slice(0, 3)
}

function getIconTone(model, compatibility) {
  if (!model?.fitsVram) return { border: 'border-orange-400/35', bg: 'bg-orange-500/10', text: 'text-orange-400' }
  if (compatibility.detail === 'Best') return { border: 'border-theme-accent/35', bg: 'bg-theme-accent/10', text: 'text-theme-accent' }
  return { border: 'border-emerald-400/30', bg: 'bg-emerald-500/10', text: 'text-emerald-400' }
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
