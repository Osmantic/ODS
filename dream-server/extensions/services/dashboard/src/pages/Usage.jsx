import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Box,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  Info,
  MoreHorizontal,
  Power,
  RefreshCw,
  Search,
  WalletCards,
} from 'lucide-react'

const PANEL_STYLE = {
  background:
    'linear-gradient(180deg, rgba(18,18,25,0.94), rgba(8,8,16,0.96)), repeating-linear-gradient(90deg, transparent 0 47px, rgba(255,255,255,0.025) 47px 48px), repeating-linear-gradient(180deg, transparent 0 47px, rgba(255,255,255,0.025) 47px 48px)',
  borderColor: 'rgba(255,255,255,0.08)',
  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.045), 0 18px 40px rgba(0,0,0,0.22)',
}

const TILE_STYLE = {
  background:
    'linear-gradient(145deg, rgba(255,255,255,0.045), rgba(255,255,255,0.018)), linear-gradient(180deg, rgba(20,20,28,0.92), rgba(10,10,18,0.95))',
  borderColor: 'rgba(255,255,255,0.08)',
}

const USAGE_TABLE_COLUMNS = 'minmax(220px,1.2fr) 112px 142px 112px 122px 96px 120px 150px 28px'
const USAGE_CHART = {
  width: 620,
  height: 150,
  left: 76,
  right: 12,
  top: 14,
  bottom: 112,
  labelY: 144,
}

const SOURCE_META = {
  actual_billed: {
    label: 'actual_billed',
    tone: 'text-purple-300 border-purple-500/30 bg-purple-500/12',
    dot: '#a855f7',
    description: 'Costs from explicit billing sources when a provider exposes them.',
  },
  priced_from_tokens: {
    label: 'priced_from_tokens',
    tone: 'text-blue-300 border-blue-500/30 bg-blue-500/12',
    dot: '#3b82f6',
    description: 'Directional estimate from configured provider pricing and tracked tokens.',
  },
  local_zero_cost: {
    label: 'local_zero_cost',
    tone: 'text-emerald-300 border-emerald-500/30 bg-emerald-500/12',
    dot: '#34d399',
    description: 'Local runtime usage. Token telemetry only, no external API bill.',
  },
  untracked: {
    label: 'untracked',
    tone: 'text-amber-300 border-amber-500/30 bg-amber-500/12',
    dot: '#f59e0b',
    description: 'No billing source or pricing available. Usage tracked, cost unknown.',
  },
}

const EMPTY_SUMMARY = {
  spend_usd: 0,
  requests: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  total_tokens: 0,
  tracked_providers: 0,
  billing_providers: 0,
  local_providers: 0,
  untracked_providers: 0,
  paid_cost_usd: 0,
  local_cost_usd: 0,
}

const EMPTY_READINESS = {
  service_id: 'token-spy',
  status: 'unknown',
  available: false,
  configured: false,
  installed: false,
  enabled: false,
  healthy: false,
  service_status: 'unknown',
  message: 'Usage tracking status is unknown.',
  detail: 'Dashboard API has not reported Token Spy readiness yet.',
  actions: {},
}

function pad2(value) {
  return String(value).padStart(2, '0')
}

function toDateKey(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

function monthRange(anchor = new Date()) {
  const start = new Date(anchor.getFullYear(), anchor.getMonth(), 1)
  const end = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0)
  return { start: toDateKey(start), end: toDateKey(end), anchor: start }
}

function addMonths(date, delta) {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1)
}

function emptyReport(start, end, detail = null) {
  const startDate = new Date(`${start}T00:00:00`)
  const endDate = new Date(`${end}T00:00:00`)
  const daily = []
  for (let cursor = new Date(startDate); cursor <= endDate; cursor.setDate(cursor.getDate() + 1)) {
    daily.push({
      date: toDateKey(cursor),
      spend_usd: 0,
      requests: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
    })
  }
  return {
    period: { start, end },
    source: { name: 'token-spy', status: 'unavailable', detail },
    summary: { ...EMPTY_SUMMARY },
    daily,
    models: [],
    services: [],
    sources: [],
  }
}

function formatCurrency(value) {
  const number = Number(value || 0)
  if (number > 0 && number < 0.001) return `$${number.toFixed(5)}`
  if (number > 0 && number < 0.01) return `$${number.toFixed(4)}`
  return `$${number.toFixed(2)}`
}

function formatCurrencyAxis(value) {
  const number = Number(value || 0)
  if (number === 0) return '$0'
  return formatCurrency(number)
}

function formatCompact(value) {
  const number = Number(value || 0)
  if (number >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(1)}B`
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}k`
  return `${Math.round(number)}`
}

function formatInteger(value) {
  return new Intl.NumberFormat('en-US').format(Math.round(Number(value || 0)))
}

function formatDateLabel(dateKey) {
  const date = new Date(`${dateKey}T00:00:00`)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatRangeLabel(range) {
  const start = new Date(`${range.start}T00:00:00`)
  const end = new Date(`${range.end}T00:00:00`)
  return `${start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${end.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
}

function computeDelta(current, previous) {
  const prev = Number(previous || 0)
  if (prev <= 0) return null
  return ((Number(current || 0) - prev) / prev) * 100
}

function buildSparkShape(values, width = 126, height = 46) {
  if (!values.length) return { line: '', area: '' }
  const numericValues = values.map(value => Number(value || 0))
  const max = Math.max(...numericValues)
  const min = Math.min(...numericValues)
  const spread = max === min ? (Math.abs(max) || 1) : max - min
  const points = numericValues.map((value, index) => {
    const x = values.length === 1 ? width : (index / (values.length - 1)) * width
    const y = height - ((value - min) / spread) * (height - 12) - 6
    return { x, y }
  })
  const line = points.map((point, index) => (
    `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  )).join(' ')
  const baseline = height - 4
  const area = `${line} L ${width.toFixed(2)} ${baseline.toFixed(2)} L 0 ${baseline.toFixed(2)} Z`
  return { line, area }
}

const USAGE_HISTORY_KEY = 'dream-usage-summary-history-v1'
let memoryUsageHistory = []

function readUsageHistory() {
  if (typeof window === 'undefined') return memoryUsageHistory
  try {
    const raw = window.localStorage?.getItem(USAGE_HISTORY_KEY)
    return raw ? JSON.parse(raw) : memoryUsageHistory
  } catch {
    return memoryUsageHistory
  }
}

function writeUsageHistory(samples) {
  memoryUsageHistory = samples
  if (typeof window === 'undefined') return
  try {
    window.localStorage?.setItem(USAGE_HISTORY_KEY, JSON.stringify(samples))
  } catch {
    // In-memory history is enough when browser storage is unavailable.
  }
}

function usageSampleFromReport(report) {
  const summary = report?.summary || EMPTY_SUMMARY
  return {
    ts: Date.now(),
    period: `${report?.period?.start || ''}:${report?.period?.end || ''}`,
    spend_usd: Number(summary.spend_usd || 0),
    total_tokens: Number(summary.total_tokens || 0),
    requests: Number(summary.requests || 0),
    tracked_providers: Number(summary.tracked_providers || 0),
    request_count_available: report?.source?.local_runtime
      ? report.source.local_runtime.request_count_available !== false
      : true,
  }
}

function appendUsageHistory(report) {
  const sample = usageSampleFromReport(report)
  const cutoff = Date.now() - 24 * 60 * 60 * 1000
  const samples = readUsageHistory()
    .filter(item => item && item.ts >= cutoff && item.period === sample.period)
  const previous = samples[samples.length - 1]
  const changed = !previous ||
    previous.spend_usd !== sample.spend_usd ||
    previous.total_tokens !== sample.total_tokens ||
    previous.requests !== sample.requests ||
    previous.tracked_providers !== sample.tracked_providers
  const next = changed ? [...samples, sample].slice(-240) : samples
  writeUsageHistory(next)
  return next
}

function seriesFromHistory(history, key, fallback = []) {
  const values = (history || []).map(sample => Number(sample?.[key] || 0))
  const hasSignal = values.some((value, index) => index > 0 && value !== values[index - 1])
  if (hasSignal) return values
  return (fallback || []).some(value => Number(value || 0) > 0) ? fallback : values
}

function useUsageReport(range, reloadToken = 0) {
  const [report, setReport] = useState(() => emptyReport(range.start, range.end))
  const [previousReport, setPreviousReport] = useState(null)
  const [readiness, setReadiness] = useState(EMPTY_READINESS)
  const [history, setHistory] = useState(() => readUsageHistory().filter(item => item.period === `${range.start}:${range.end}`))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const prevRange = monthRange(addMonths(range.anchor, -1))

    async function load({ silent = false } = {}) {
      if (!silent) setLoading(true)
      if (!silent) setError(null)
      try {
        const [currentRes, previousRes, readinessRes] = await Promise.all([
          fetch(`/api/usage/report?start=${range.start}&end=${range.end}`),
          fetch(`/api/usage/report?start=${prevRange.start}&end=${prevRange.end}`),
          fetch('/api/usage/readiness'),
        ])
        if (!currentRes.ok) throw new Error(`Usage API returned HTTP ${currentRes.status}`)
        const current = await currentRes.json()
        const previous = previousRes.ok ? await previousRes.json() : null
        const usageReadiness = readinessRes.ok ? await readinessRes.json() : {
          ...EMPTY_READINESS,
          status: 'unavailable',
          detail: `Usage readiness API returned HTTP ${readinessRes.status}`,
        }
        if (!cancelled) {
          setReport({
            ...emptyReport(range.start, range.end),
            ...current,
            summary: { ...EMPTY_SUMMARY, ...(current.summary || {}) },
          })
          setReadiness({ ...EMPTY_READINESS, ...usageReadiness, actions: usageReadiness.actions || {} })
          setHistory(appendUsageHistory(current))
          setPreviousReport(previous ? {
            ...emptyReport(prevRange.start, prevRange.end),
            ...previous,
            summary: { ...EMPTY_SUMMARY, ...(previous.summary || {}) },
          } : null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message)
          setReport(emptyReport(range.start, range.end, err.message))
          setReadiness({ ...EMPTY_READINESS, status: 'unavailable', detail: err.message })
          setPreviousReport(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    // Skip ticks while the tab is hidden; refresh immediately on return (#1490)
    const tick = () => { if (!document.hidden) load({ silent: true }) }
    const intervalId = window.setInterval(tick, 10000)
    const onVisibility = () => { if (!document.hidden) load({ silent: true }) }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [range, reloadToken])

  return { report, previousReport, readiness, history, loading, error }
}

export default function Usage({ status }) {
  const [rangeAnchor, setRangeAnchor] = useState(() => monthRange().anchor)
  const [reloadToken, setReloadToken] = useState(0)
  const [actionState, setActionState] = useState(null)
  const range = useMemo(() => monthRange(rangeAnchor), [rangeAnchor])
  const { report, previousReport, readiness, history, loading, error } = useUsageReport(range, reloadToken)
  const summary = report.summary || EMPTY_SUMMARY
  const previous = previousReport?.summary || EMPTY_SUMMARY
  const dailySpend = report.daily?.map(day => day.spend_usd || 0) || []
  const dailyRequests = report.daily?.map(day => day.requests || 0) || []
  const dailyProviders = report.daily?.map(day => (
    (day.input_tokens || 0) + (day.output_tokens || 0) + (day.cache_read_tokens || 0)
  )) || []
  const sourceStatus = report.source?.status || 'unavailable'
  const modelName = status?.inference?.loadedModel || status?.model?.name || 'local model'
  const requestsUnavailable = summary.requests === 0 &&
    summary.total_tokens > 0 &&
    report.source?.local_runtime &&
    report.source.local_runtime.request_count_available === false
  const spendSpark = seriesFromHistory(history, 'spend_usd', dailySpend)
  const tokensSpark = seriesFromHistory(history, 'total_tokens', dailyProviders)
  const requestsSpark = requestsUnavailable ? [] : seriesFromHistory(history, 'requests', dailyRequests)
  const showReadinessPanel = readiness.status !== 'ready' && !(loading && readiness.status === 'unknown')
  const showGenericSourceWarning = (error || sourceStatus !== 'ok') && readiness.status === 'ready'

  async function runUsageAction(kind) {
    const action = readiness.actions?.[kind]
    if (!action?.url) return
    setActionState({ status: 'running', kind, message: action.label })
    try {
      const response = await fetch(action.url, { method: action.method || 'POST' })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || `Action failed with HTTP ${response.status}`)
      }
      setActionState({
        status: 'success',
        kind,
        message: payload.message || `${action.label} request accepted.`,
      })
      window.setTimeout(() => setReloadToken(value => value + 1), 1200)
    } catch (err) {
      setActionState({ status: 'error', kind, message: err.message })
    }
  }

  return (
    <div className="min-h-screen px-4 py-4 text-theme-text sm:px-5 xl:px-6 xl:py-5">
      <header className="mb-1.5 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-[22px] font-bold leading-tight tracking-tight text-theme-text">Usage</h1>
          <p className="mt-0.5 text-sm text-theme-text-secondary">
            Track tokens, requests, and explicit or estimated provider costs across your AI services.
          </p>
          <p className="mt-1.5 text-xs text-theme-text-muted">
            Cost values are directional unless a provider billing source exists. Local runtime counters stay token-only and are not priced by DreamServer.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center lg:flex-col lg:items-end">
          <div className="rounded-lg border border-white/10 bg-black/20 px-3 py-1.5 font-mono text-[10px] text-theme-text-muted">
            <span className="text-theme-accent-light">{status?.tier || 'Minimal'}</span>
            <span className="mx-2 text-theme-text-muted/60">{modelName}</span>
            <span>v{status?.version || '...'}</span>
          </div>
          <MonthSelector
            range={range}
            onPrevious={() => setRangeAnchor(current => addMonths(current, -1))}
            onNext={() => setRangeAnchor(current => addMonths(current, 1))}
          />
        </div>
      </header>

      {showReadinessPanel && (
        <UsageReadinessPanel
          readiness={readiness}
          actionState={actionState}
          onAction={runUsageAction}
        />
      )}

      {showGenericSourceWarning && (
        <div className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/[0.08] px-4 py-3 text-xs text-amber-200">
          {report.source?.detail || error || 'No tracked usage for this period'}
        </div>
      )}

      <section className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          icon={WalletCards}
          title="Cost Estimate"
          value={formatCurrency(summary.spend_usd)}
          unit="USD"
          subvalue="Directional, not a bill"
          delta={computeDelta(summary.spend_usd, previous.spend_usd)}
          series={spendSpark}
          accent="#9d00ff"
          loading={loading}
        />
        <TokensSummaryCard
          summary={summary}
          previous={previous}
          series={tokensSpark}
          loading={loading}
        />
        <SummaryCard
          icon={Activity}
          title="Requests"
          value={requestsUnavailable ? '-' : formatInteger(summary.requests)}
          subvalue={requestsUnavailable ? 'Runtime counter unavailable' : undefined}
          delta={computeDelta(summary.requests, previous.requests)}
          series={requestsSpark}
          accent="#8b5cf6"
          loading={loading}
        />
        <SummaryCard
          icon={Database}
          title="Tracked Providers"
          value={formatInteger(summary.tracked_providers)}
          subvalue={`${summary.billing_providers || 0} billing - ${summary.local_providers || 0} local - ${summary.untracked_providers || 0} untracked`}
          series={[]}
          accent="#9d00ff"
          loading={loading}
        />
      </section>

      <section className="mb-3 grid grid-cols-1 gap-3 xl:grid-cols-12">
        <SpendPanel report={report} className="xl:col-span-4" />
        <TokensPanel report={report} className="xl:col-span-5" />
        <CostConfidencePanel summary={summary} className="xl:col-span-3" />
      </section>

      <CostSourceGuide />

      <section className="mt-3 grid grid-cols-1 gap-3 2xl:grid-cols-[minmax(0,1fr)_390px]">
        <UsageByModelTable rows={report.models || []} />
        <div className="grid gap-3">
          <TopConsumers services={report.services || []} />
          <TokensByService services={report.services || []} totalTokens={summary.total_tokens || 0} />
        </div>
      </section>
    </div>
  )
}

function MonthSelector({ range, onPrevious, onNext }) {
  return (
    <div className="flex h-10 items-center rounded-lg border border-white/10 bg-black/25">
      <button type="button" onClick={onPrevious} className="px-2.5 text-theme-text-muted hover:text-theme-text" aria-label="Previous month">
        <ChevronLeft size={16} />
      </button>
      <div className="flex min-w-[190px] items-center justify-center gap-2 border-x border-white/10 px-3 text-xs font-semibold text-theme-text">
        <Calendar size={14} className="text-theme-text-muted" />
        {formatRangeLabel(range)}
      </div>
      <button type="button" onClick={onNext} className="px-2.5 text-theme-text-muted hover:text-theme-text" aria-label="Next month">
        <ChevronRight size={16} />
      </button>
    </div>
  )
}

function UsageReadinessPanel({ readiness, actionState, onAction }) {
  const actions = readiness.actions || {}
  const busy = actionState?.status === 'running'
  const tone = readiness.status === 'missing' || readiness.status === 'unconfigured'
    ? 'border-red-500/25 bg-red-500/[0.08] text-red-100'
    : 'border-amber-500/25 bg-amber-500/[0.08] text-amber-100'
  return (
    <section className={`mb-3 rounded-xl border px-4 py-3 ${tone}`}>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Activity size={15} />
            <h2 className="font-semibold text-theme-text">{readiness.message || 'Usage tracking needs attention.'}</h2>
          </div>
          <p className="mt-1 text-xs leading-5 text-theme-text-secondary">
            {readiness.detail || 'Token Spy must be enabled and healthy before Usage can show complete real telemetry.'}
          </p>
          <p className="mt-1 text-[11px] text-theme-text-muted">
            Token Spy: {readiness.enabled ? 'enabled' : 'not enabled'} · health: {readiness.service_status || 'unknown'}
          </p>
          {actionState?.status && actionState.status !== 'running' && (
            <p className={`mt-2 text-xs ${actionState.status === 'error' ? 'text-red-300' : 'text-emerald-300'}`}>
              {actionState.message}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {actions.enable?.url && (
            <button
              type="button"
              onClick={() => onAction('enable')}
              disabled={busy}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-theme-accent/40 bg-theme-accent/20 px-3 text-xs font-semibold text-theme-accent-light hover:bg-theme-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Power size={14} />
              {busy && actionState?.kind === 'enable' ? 'Working...' : actions.enable.label || 'Enable Usage Tracking'}
            </button>
          )}
          {actions.restart?.url && (
            <button
              type="button"
              onClick={() => onAction('restart')}
              disabled={busy}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-white/10 bg-black/20 px-3 text-xs font-semibold text-theme-text hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw size={14} />
              {busy && actionState?.kind === 'restart' ? 'Working...' : actions.restart.label || 'Restart Token Spy'}
            </button>
          )}
        </div>
      </div>
    </section>
  )
}

function Panel({ children, className = '' }) {
  return (
    <section className={`rounded-xl border ${className}`} style={PANEL_STYLE}>
      {children}
    </section>
  )
}

function SummaryCard({ icon: Icon, title, value, unit, subvalue, delta, series, accent, loading }) {
  const shape = buildSparkShape(series || [])
  const deltaTone = delta == null ? 'text-theme-text-muted' : delta >= 0 ? 'text-emerald-400' : 'text-red-400'
  return (
    <Panel>
      <div className="flex min-h-[90px] items-center justify-between gap-3 p-3">
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-theme-accent/25 text-theme-accent-light">
              <Icon size={15} />
            </div>
            <h2 className="whitespace-nowrap font-semibold text-theme-text">{title}</h2>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[24px] font-bold leading-none text-theme-text">{loading ? '...' : value}</span>
            {unit && <span className="text-lg font-semibold text-theme-text-muted">{unit}</span>}
          </div>
          <p className={`mt-1.5 text-xs ${deltaTone}`}>
            {delta == null ? (subvalue || 'No prior period') : `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}% vs prior month`}
          </p>
        </div>
        <Sparkline shape={shape} accent={accent} />
      </div>
    </Panel>
  )
}

function TokensSummaryCard({ summary, previous, series, loading }) {
  const shape = buildSparkShape(series || [])
  const delta = computeDelta(summary.total_tokens, previous.total_tokens)
  return (
    <Panel>
      <div className="min-h-[90px] p-2">
        <div className="mb-1.5 flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-theme-accent/25 text-theme-accent-light">
            <Box size={15} />
          </div>
          <h2 className="whitespace-nowrap font-semibold text-theme-text">Tokens</h2>
          <div className="ml-auto">
            <Sparkline shape={shape} accent="#9d00ff" compact />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <TokenMetric label="Input" value={loading ? '...' : formatCompact(summary.input_tokens)} tone="text-theme-accent-light" />
          <TokenMetric label="Output" value={loading ? '...' : formatCompact(summary.output_tokens)} tone="text-blue-400" />
          <TokenMetric label="Cache Read" value={loading ? '...' : formatCompact(summary.cache_read_tokens)} tone="text-emerald-400" />
        </div>
        <p className="mt-1 text-xs text-theme-text-muted">
          {formatCompact(summary.total_tokens)} total
          {delta != null && <span className={delta >= 0 ? 'ml-2 text-emerald-400' : 'ml-2 text-red-400'}>{delta >= 0 ? '+' : ''}{delta.toFixed(1)}%</span>}
        </p>
      </div>
    </Panel>
  )
}

function TokenMetric({ label, value, tone }) {
  return (
    <div>
      <p className="text-[10px] text-theme-text-muted">{label}</p>
      <p className={`mt-0.5 font-mono text-base font-bold ${tone}`}>{value}</p>
    </div>
  )
}

function Sparkline({ shape, accent, compact = false }) {
  const id = `spark-${accent.replace('#', '')}`
  return (
    <svg viewBox="0 0 126 46" className={`${compact ? 'h-8 w-28' : 'h-12 w-36'} shrink-0 overflow-visible`}>
      <defs>
        <linearGradient id={`${id}-line`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor={accent} stopOpacity="0.75" />
          <stop offset="100%" stopColor="#d8b4fe" stopOpacity="1" />
        </linearGradient>
        <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={accent} stopOpacity="0.42" />
          <stop offset="100%" stopColor={accent} stopOpacity="0.02" />
        </linearGradient>
        <filter id={`${id}-glow`} x="-20%" y="-80%" width="140%" height="260%">
          <feGaussianBlur stdDeviation="2.2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {shape?.line ? (
        <>
          <path d={shape.area} fill={`url(#${id}-fill)`} />
          <path d={shape.line} fill="none" stroke={`url(#${id}-line)`} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" filter={`url(#${id}-glow)`} />
        </>
      ) : (
        <line x1="8" x2="118" y1="34" y2="34" stroke="rgba(255,255,255,0.12)" strokeDasharray="4 5" />
      )}
    </svg>
  )
}

function SpendPanel({ report, className }) {
  const [mode, setMode] = useState('daily')
  const daily = report.daily || []
  const values = useMemo(() => {
    if (mode === 'cumulative') {
      let running = 0
      return daily.map(day => {
        running += day.spend_usd || 0
        return { label: formatDateLabel(day.date), value: running }
      })
    }
    if (mode === 'weekly') {
      const weeks = []
      daily.forEach((day, index) => {
        const weekIndex = Math.floor(index / 7)
        if (!weeks[weekIndex]) weeks[weekIndex] = { label: `W${weekIndex + 1}`, value: 0 }
        weeks[weekIndex].value += day.spend_usd || 0
      })
      return weeks
    }
    return daily.map(day => ({ label: formatDateLabel(day.date), value: day.spend_usd || 0 }))
  }, [daily, mode])
  return (
    <Panel className={className}>
      <div className="p-3">
        <div className="flex min-h-8 items-center justify-between gap-3">
          <PanelHeader title="Daily Cost Estimate" info />
          <Segmented tabs={['daily', 'weekly', 'cumulative']} active={mode} onChange={setMode} />
        </div>
        <div className="mt-2 h-10">
          <p className="font-mono text-xl font-bold leading-none">{formatCurrency(report.summary?.spend_usd || 0)}</p>
          <p className="mt-1 text-xs text-theme-text-muted">estimated total</p>
        </div>
        <BarChart values={values} color="#8b35d6" currency />
      </div>
    </Panel>
  )
}

function TokensPanel({ report, className }) {
  return (
    <Panel className={className}>
      <div className="p-3">
        <div className="flex min-h-8 items-center justify-between gap-3">
          <PanelHeader title="Tokens per Day" info />
          <div className="flex flex-wrap justify-end gap-3 text-[11px] text-theme-text-muted">
            <Legend color="#8b35d6" label="Input" />
            <Legend color="#3b82f6" label="Output" />
            <Legend color="#34d399" label="Cache Read" />
          </div>
        </div>
        <div className="mt-2 h-10">
          <p className="font-mono text-xl font-bold leading-none">{formatCompact(report.summary?.total_tokens || 0)} <span className="text-sm text-theme-text-muted">total</span></p>
        </div>
        <StackedTokenChart daily={report.daily || []} />
      </div>
    </Panel>
  )
}

function CostConfidencePanel({ summary, className }) {
  const paid = Number(summary.paid_cost_usd || 0)
  const local = Number(summary.local_cost_usd || 0)
  const untracked = Number(summary.untracked_providers || 0)
  const total = Math.max(paid + local, paid, 1)
  return (
    <Panel className={className}>
      <div className="p-3">
        <PanelHeader title="Cost Confidence" info />
        <div className="mt-3 grid gap-3 sm:grid-cols-[124px_1fr] xl:grid-cols-1 2xl:grid-cols-[124px_1fr]">
          <DonutChart
            total={paid + local}
            segments={[
              { value: paid, color: '#8b35d6' },
              { value: local, color: '#34d399' },
            ]}
            centerTop="Estimate"
            centerMain={formatCurrency(paid + local)}
            centerBottom="Total"
          />
          <div className="space-y-4 pt-2 text-sm">
            <CostSplitRow color="#8b35d6" label="Provider estimate" value={paid} total={total} />
            <CostSplitRow color="#34d399" label="Local API bill" value={local} total={total} />
            <div className="rounded-md border border-amber-500/20 bg-amber-500/[0.07] px-2 py-1.5 text-xs text-amber-100">
              {formatInteger(untracked)} untracked provider{untracked === 1 ? '' : 's'}
            </div>
          </div>
        </div>
        <p className="mt-1.5 text-xs text-theme-text-muted">Provider prices can vary by plan, cache behavior, region, and time. Treat these as telemetry, not invoices.</p>
      </div>
    </Panel>
  )
}

function CostSourceGuide() {
  const localMeta = SOURCE_META.local_zero_cost
  const guideItems = [
    ['actual_billed', {
      ...SOURCE_META.actual_billed,
      description: 'Explicit billing data when available.',
    }],
    ['priced_from_tokens', {
      ...SOURCE_META.priced_from_tokens,
      description: 'Configured provider pricing times tracked tokens.',
    }],
    ['local_combined', {
      ...localMeta,
      label: 'local_zero_cost',
      tone: localMeta.tone,
      description: 'Local runtime telemetry. No external API bill.',
    }],
    ['untracked', {
      ...SOURCE_META.untracked,
      description: 'No billing or pricing source. Cost unknown.',
    }],
  ]
  return (
    <Panel>
      <div className="grid gap-2 p-2 lg:grid-cols-2 xl:grid-cols-[220px_repeat(5,minmax(0,1fr))]">
        <div className="lg:col-span-2 xl:col-span-1">
          <h2 className="font-semibold text-theme-text">Tracking Source Guide</h2>
          <p className="mt-1 text-xs text-theme-text-muted">Where token and cost signals come from.</p>
        </div>
        {guideItems.map(([key, meta]) => (
          <div key={key} className="rounded-lg border border-white/10 bg-black/15 p-1.5">
            <span className={`inline-flex rounded-full border px-2 py-0.5 font-mono text-[11px] ${meta.tone}`}>{meta.label}</span>
            <p className="mt-1.5 text-[11px] leading-3.5 text-theme-text-muted">{meta.description}</p>
          </div>
        ))}
        <div className="group relative rounded-lg border border-purple-500/20 bg-purple-500/[0.08] p-1.5">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Info size={14} className="text-theme-text-muted" />
            Honest by design
          </div>
          <p className="mt-1 text-[11px] leading-3.5 text-theme-text-muted">No reliable source? It stays untracked.</p>
          <button type="button" className="mt-1 text-left text-xs font-semibold text-theme-text-secondary hover:text-theme-text">
            Learn more -&gt;
          </button>
          <div className="pointer-events-none absolute bottom-full right-0 z-20 mb-2 hidden w-72 rounded-lg border border-white/10 bg-[#101018] p-3 text-xs leading-5 text-theme-text-secondary shadow-2xl group-hover:block group-focus-within:block">
            Token counts come from Token Spy or llama.cpp Prometheus counters. Cost estimates use configured provider pricing only; local runtime counters are token-only. If neither source exists, DreamServer leaves the row untracked.
          </div>
        </div>
      </div>
    </Panel>
  )
}

function UsageByModelTable({ rows }) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const [service, setService] = useState('all')
  const [source, setSource] = useState('all')
  const [page, setPage] = useState(0)
  const providers = useMemo(() => ['all', ...new Set(rows.map(row => row.provider || 'unknown'))], [rows])
  const services = useMemo(() => ['all', ...new Set(rows.map(row => row.service || 'unknown'))], [rows])
  const sources = useMemo(() => ['all', ...new Set(rows.map(row => row.cost_source || 'untracked'))], [rows])
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return rows.filter(row => {
      if (provider !== 'all' && (row.provider || 'unknown') !== provider) return false
      if (service !== 'all' && (row.service || 'unknown') !== service) return false
      if (source !== 'all' && (row.cost_source || 'untracked') !== source) return false
      if (!q) return true
      return [row.model, row.provider, row.service, row.cost_source].some(value => String(value || '').toLowerCase().includes(q))
    })
  }, [provider, query, rows, service, source])
  const pageSize = 10
  const pageCount = Math.max(Math.ceil(filtered.length / pageSize), 1)
  const safePage = Math.min(page, pageCount - 1)
  const visible = filtered.slice(safePage * pageSize, safePage * pageSize + pageSize)

  useEffect(() => { setPage(0) }, [provider, query, service, source])

  const exportCsv = () => {
    const header = ['model', 'provider', 'service', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'requests', 'cost_usd', 'cost_source']
    const lines = [
      header.join(','),
      ...filtered.map(row => header.map(key => JSON.stringify(row[key] ?? '')).join(',')),
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'dream-usage-by-model.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Panel className="min-h-[330px]">
      <div className="flex min-h-[330px] flex-col p-3.5">
        <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <h2 className="whitespace-nowrap text-lg font-semibold">Usage by Model</h2>
          <div className="grid gap-2 lg:grid-cols-[minmax(160px,1fr)_136px_136px_136px_auto]">
            <SearchBox value={query} onChange={setQuery} />
            <Select value={provider} onChange={setProvider} options={providers} label="All Providers" />
            <Select value={service} onChange={setService} options={services} label="All Services" />
            <Select value={source} onChange={setSource} options={sources} label="All Sources" />
            <button type="button" onClick={exportCsv} className="flex h-9 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-white/10 bg-black/20 px-3 text-xs font-semibold text-theme-text hover:bg-white/5">
              <Download size={14} /> Export CSV
            </button>
          </div>
        </div>
        <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-white/[0.08]">
          <div className="hidden border-b border-white/[0.08] px-3 py-2 text-[9px] font-semibold uppercase tracking-[0.08em] text-theme-text-muted/60 xl:grid" style={{ gridTemplateColumns: USAGE_TABLE_COLUMNS }}>
            <span>Model</span>
            <span>Provider</span>
            <span>Service</span>
            <span className="text-right">Input Tokens</span>
            <span className="text-right">Output Tokens</span>
            <span className="text-right">Requests</span>
            <span className="text-right">Est. Cost</span>
            <span className="pl-4">Source</span>
            <span />
          </div>
          {visible.length > 0 ? visible.map(row => (
            <UsageRow key={`${row.model}-${row.provider}-${row.service}-${row.cost_source}`} row={row} />
          )) : (
            <div className="flex flex-1 items-center justify-center px-4 py-10 text-center text-sm text-theme-text-muted">No tracked usage for this period</div>
          )}
        </div>
        <div className="mt-3 flex items-center justify-between text-xs text-theme-text-muted">
          <span>Showing {filtered.length ? safePage * pageSize + 1 : 0} to {Math.min((safePage + 1) * pageSize, filtered.length)} of {filtered.length} models</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => setPage(Math.max(safePage - 1, 0))} className="rounded border border-white/10 px-2 py-1 hover:bg-white/5">&lt;</button>
            <span className="rounded border border-theme-accent/50 bg-theme-accent/15 px-3 py-1 text-theme-accent-light">{safePage + 1}</span>
            <span>{pageCount}</span>
            <button type="button" onClick={() => setPage(Math.min(safePage + 1, pageCount - 1))} className="rounded border border-white/10 px-2 py-1 hover:bg-white/5">&gt;</button>
          </div>
        </div>
      </div>
    </Panel>
  )
}

function UsageRow({ row }) {
  const meta = SOURCE_META[row.cost_source] || SOURCE_META.untracked
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-white/[0.055] px-3 py-2.5 text-sm last:border-b-0 xl:gap-0 xl:items-center xl:[grid-template-columns:var(--usage-table-columns)]" style={{ '--usage-table-columns': USAGE_TABLE_COLUMNS }}>
      <div className="min-w-0">
        <p className="truncate font-semibold text-theme-text">{row.model || 'unknown'}</p>
        <p className="mt-0.5 text-xs text-theme-text-muted">{formatCompact((row.input_tokens || 0) + (row.output_tokens || 0) + (row.cache_read_tokens || 0))} tracked tokens</p>
      </div>
      <Cell label="Provider">{row.provider || 'unknown'}</Cell>
      <Cell label="Service">{row.service || 'unknown'}</Cell>
      <Cell label="Input" align="right">{formatCompact(row.input_tokens)}</Cell>
      <Cell label="Output" align="right">{formatCompact(row.output_tokens)}</Cell>
      <Cell label="Requests" align="right">{formatInteger(row.requests)}</Cell>
      <Cell label="Est. Cost" align="right">{row.cost_source === 'untracked' ? '-' : formatCurrency(row.cost_usd)}</Cell>
      <Cell label="Source" className="xl:pl-4"><span className={`rounded border px-2 py-0.5 font-mono text-[11px] ${meta.tone}`}>{meta.label}</span></Cell>
      <MoreHorizontal size={16} className="text-theme-text-muted" />
    </div>
  )
}

function Cell({ label, children, align = 'left', className = '' }) {
  const desktopAlign = align === 'right' ? 'xl:text-right' : 'xl:text-left'
  return (
    <div className={`flex min-w-0 justify-between gap-3 xl:block ${desktopAlign} ${className}`}>
      <span className="text-xs uppercase tracking-[0.16em] text-theme-text-muted/50 xl:hidden">{label}</span>
      <span className="truncate font-mono text-theme-text-secondary">{children}</span>
    </div>
  )
}

function TopConsumers({ services }) {
  const top = services.slice(0, 5)
  const max = Math.max(...top.map(service => serviceTokens(service)), 1)
  return (
    <Panel>
      <div className="p-3.5">
        <h2 className="mb-3 font-semibold">Top Consumers by Tokens</h2>
        <div className="space-y-3">
          {top.length > 0 ? top.map(service => (
            <div key={service.service} className="grid grid-cols-[120px_1fr_70px] items-center gap-3 text-sm">
              <span className="truncate text-theme-text-secondary">{service.service}</span>
              <div className="h-1.5 rounded-full bg-white/[0.08]">
                <div className="h-full rounded-full bg-theme-accent" style={{ width: `${(serviceTokens(service) / max) * 100}%` }} />
              </div>
              <span className="text-right font-mono">{formatCompact(serviceTokens(service))}</span>
            </div>
          )) : (
            <p className="py-5 text-sm text-theme-text-muted">No tracked usage for this period</p>
          )}
        </div>
        <p className="mt-3 text-right text-xs text-theme-text-muted">View all services -&gt;</p>
      </div>
    </Panel>
  )
}

function serviceTokens(service) {
  return Number(service.input_tokens || 0) + Number(service.output_tokens || 0) + Number(service.cache_read_tokens || 0) + Number(service.cache_write_tokens || 0)
}

function TokensByService({ services, totalTokens }) {
  const top = services.slice(0, 4)
  const otherTokens = Math.max((totalTokens || 0) - top.reduce((sum, item) => sum + serviceTokens(item), 0), 0)
  const segments = [
    ...top.map((service, index) => ({ label: service.service, value: serviceTokens(service), color: ['#8b35d6', '#3b82f6', '#34d399', '#c4c4c4'][index] })),
    ...(otherTokens > 0 ? [{ label: 'Other', value: otherTokens, color: '#a1a1aa' }] : []),
  ]
  return (
    <Panel>
      <div className="p-3.5">
        <h2 className="mb-3 font-semibold">Tokens by Service</h2>
        <div className="grid gap-3 sm:grid-cols-[128px_1fr] xl:grid-cols-1 2xl:grid-cols-[128px_1fr]">
          <DonutChart total={totalTokens || 0} segments={segments} centerMain={formatCompact(totalTokens)} centerBottom="tokens" />
          <div className="space-y-3">
            {segments.length > 0 ? segments.map(segment => (
              <CostSplitRow key={segment.label} color={segment.color} label={segment.label} value={segment.value} total={totalTokens || 0} formatter={formatCompact} />
            )) : (
              <p className="text-sm text-theme-text-muted">No service token data</p>
            )}
          </div>
        </div>
        <p className="mt-3 text-right text-xs text-theme-text-muted">View full breakdown -&gt;</p>
      </div>
    </Panel>
  )
}

function PanelHeader({ title, info = false }) {
  return (
    <div className="flex items-center gap-2">
      <h2 className="font-semibold text-theme-text">{title}</h2>
      {info && <Info size={13} className="text-theme-text-muted" />}
    </div>
  )
}

function Segmented({ tabs, active, onChange }) {
  return (
    <div className="flex rounded-md border border-white/10 bg-black/20 p-0.5">
      {tabs.map(tab => (
        <button
          key={tab}
          type="button"
          onClick={() => onChange(tab)}
          className={`rounded px-2.5 py-1 text-[11px] capitalize ${active === tab ? 'bg-theme-accent/25 text-theme-accent-light' : 'text-theme-text-muted hover:text-theme-text'}`}
        >
          {tab}
        </button>
      ))}
    </div>
  )
}

function Legend({ color, label }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  )
}

function SearchBox({ value, onChange }) {
  return (
    <label className="flex h-9 items-center gap-2 rounded-md border border-white/10 bg-black/20 px-3">
      <Search size={14} className="text-theme-text-muted" />
      <input
        value={value}
        onChange={event => onChange(event.target.value)}
        placeholder="Search models..."
        className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-theme-text-muted/60"
      />
    </label>
  )
}

function Select({ value, onChange, options, label }) {
  return (
    <select
      value={value}
      onChange={event => onChange(event.target.value)}
      className="h-9 rounded-md border border-white/10 bg-[#080810] px-3 text-xs text-theme-text outline-none"
      aria-label={label}
    >
      {options.map(option => (
        <option key={option} value={option}>{option === 'all' ? label : option}</option>
      ))}
    </select>
  )
}

function BarChart({ values, color, currency = false }) {
  const actualMax = Math.max(...values.map(item => item.value), 0)
  const max = actualMax > 0 ? actualMax : 1
  const labelStep = Math.max(Math.ceil(values.length / 6), 1)
  const plotWidth = USAGE_CHART.width - USAGE_CHART.left - USAGE_CHART.right
  const plotHeight = USAGE_CHART.bottom - USAGE_CHART.top
  const ticks = [1, 0.75, 0.5, 0.25, 0]
  return (
    <svg viewBox={`0 0 ${USAGE_CHART.width} ${USAGE_CHART.height}`} preserveAspectRatio="none" className="mt-2 h-[116px] w-full overflow-visible">
      {ticks.map(ratio => {
        const y = USAGE_CHART.top + (1 - ratio) * plotHeight
        return (
          <g key={ratio}>
            <line x1={USAGE_CHART.left} x2={USAGE_CHART.left + plotWidth} y1={y} y2={y} stroke="rgba(255,255,255,0.08)" />
            <text x="0" y={y + 3} fill="rgba(255,255,255,0.5)" fontSize="9">{currency ? formatCurrencyAxis(ratio * actualMax) : formatCompact(ratio * actualMax)}</text>
          </g>
        )
      })}
      {values.map((item, index) => {
        const slot = plotWidth / Math.max(values.length, 1)
        const width = Math.max(slot * 0.46, 3)
        const height = ((item.value || 0) / max) * (plotHeight - 6)
        const x = USAGE_CHART.left + index * slot + (slot - width) / 2
        const y = USAGE_CHART.bottom - height
        return (
          <g key={`${item.label}-${index}`}>
            <rect x={x} y={y} width={width} height={height} rx="1.5" fill={color} opacity="0.85" />
            {index % labelStep === 0 && <text x={x + width / 2} y={USAGE_CHART.labelY} fill="rgba(255,255,255,0.52)" fontSize="8.5" textAnchor="middle">{item.label}</text>}
          </g>
        )
      })}
    </svg>
  )
}

function StackedTokenChart({ daily }) {
  const actualMax = Math.max(...daily.map(day => (day.input_tokens || 0) + (day.output_tokens || 0) + (day.cache_read_tokens || 0)), 0)
  const max = Math.max(actualMax, 1)
  const labelStep = Math.max(Math.ceil(daily.length / 6), 1)
  const plotWidth = USAGE_CHART.width - USAGE_CHART.left - USAGE_CHART.right
  const plotHeight = USAGE_CHART.bottom - USAGE_CHART.top
  const ticks = [1, 0.75, 0.5, 0.25, 0]
  return (
    <svg viewBox={`0 0 ${USAGE_CHART.width} ${USAGE_CHART.height}`} preserveAspectRatio="none" className="mt-2 h-[116px] w-full overflow-visible">
      {ticks.map(ratio => {
        const y = USAGE_CHART.top + (1 - ratio) * plotHeight
        return (
          <g key={ratio}>
            <line x1={USAGE_CHART.left} x2={USAGE_CHART.left + plotWidth} y1={y} y2={y} stroke="rgba(255,255,255,0.08)" />
            <text x="0" y={y + 3} fill="rgba(255,255,255,0.5)" fontSize="9">{formatCompact(ratio * actualMax)}</text>
          </g>
        )
      })}
      {daily.map((day, index) => {
        const slot = plotWidth / Math.max(daily.length, 1)
        const width = Math.max(slot * 0.46, 3)
        const x = USAGE_CHART.left + index * slot + (slot - width) / 2
        let y = USAGE_CHART.bottom
        const parts = [
          { value: day.cache_read_tokens || 0, color: '#34d399' },
          { value: day.output_tokens || 0, color: '#3b82f6' },
          { value: day.input_tokens || 0, color: '#8b35d6' },
        ]
        return (
          <g key={day.date}>
            {parts.map((part, partIndex) => {
              const h = (part.value / max) * (plotHeight - 6)
              y -= h
              return <rect key={partIndex} x={x} y={y} width={width} height={h} rx="1.5" fill={part.color} opacity="0.9" />
            })}
            {index % labelStep === 0 && <text x={x + width / 2} y={USAGE_CHART.labelY} fill="rgba(255,255,255,0.52)" fontSize="8.5" textAnchor="middle">{formatDateLabel(day.date)}</text>}
          </g>
        )
      })}
    </svg>
  )
}

function DonutChart({ total, segments, centerTop, centerMain, centerBottom }) {
  const radius = 45
  const strokeWidth = 12
  const circumference = 2 * Math.PI * radius
  let offset = 0
  const hasData = total > 0
  return (
    <svg viewBox="0 0 132 132" className="h-30 w-30">
      <circle cx="66" cy="66" r={radius} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={strokeWidth} />
      {hasData && segments.map((segment, index) => {
        const length = (segment.value / total) * circumference
        const circle = (
          <circle
            key={`${segment.color}-${index}`}
            cx="66"
            cy="66"
            r={radius}
            fill="none"
            stroke={segment.color}
            strokeWidth={strokeWidth}
            strokeDasharray={`${length} ${circumference - length}`}
            strokeDashoffset={-offset}
            strokeLinecap="butt"
            transform="rotate(-90 66 66)"
          />
        )
        offset += length
        return circle
      })}
      <text x="66" y="54" fill="rgba(255,255,255,0.6)" fontSize="9" textAnchor="middle">{centerTop || ''}</text>
      <text x="66" y="72" fill="white" fontSize="16" fontWeight="700" textAnchor="middle">{centerMain}</text>
      <text x="66" y="88" fill="rgba(255,255,255,0.55)" fontSize="10" textAnchor="middle">{centerBottom}</text>
    </svg>
  )
}

function CostSplitRow({ color, label, value, total, formatter = formatCurrency }) {
  const pct = total > 0 ? (value / total) * 100 : 0
  return (
    <div className="grid grid-cols-[1fr_auto] gap-3 text-xs">
      <div className="flex min-w-0 items-center gap-2 text-theme-text-secondary">
        <span className="h-2 w-2 rounded-full" style={{ background: color }} />
        <span className="truncate">{label}</span>
      </div>
      <div className="text-right font-mono text-theme-text">
        <div>{formatter(value)}</div>
        <div className="text-theme-text-muted">{pct.toFixed(1)}%</div>
      </div>
    </div>
  )
}
