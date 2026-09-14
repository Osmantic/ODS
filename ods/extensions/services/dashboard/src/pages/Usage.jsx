import UsageView from '../components/usage/UsageView'
import {useEffect, useMemo, useState} from 'react'

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

const USAGE_HISTORY_KEY = 'ods-usage-summary-history-v1'
// Per-period sparkline depth, and an overall guard so the entry never grows
// without limit once several periods share the store.
const HISTORY_SAMPLES_PER_PERIOD = 240
const HISTORY_SAMPLES_TOTAL = 960
let memoryUsageHistory = []

function readUsageHistory() {
  if (typeof window === 'undefined') return memoryUsageHistory
  try {
    const raw = window.localStorage?.getItem(USAGE_HISTORY_KEY)
    const parsed = raw ? JSON.parse(raw) : memoryUsageHistory
    return Array.isArray(parsed) ? parsed.filter(Boolean) : memoryUsageHistory
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
  // Age out everything first, then split by period. Samples belonging to other
  // periods are carried through untouched: the store is keyed by period (the
  // month selector reads it back that way), so persisting only the period we
  // just fetched would erase the sparkline history of the month the user
  // navigated away from.
  const live = readUsageHistory().filter(item => item && item.ts >= cutoff)
  const samples = live.filter(item => item.period === sample.period)
  const others = live.filter(item => item.period !== sample.period)
  const previous = samples[samples.length - 1]
  const changed = !previous ||
    previous.spend_usd !== sample.spend_usd ||
    previous.total_tokens !== sample.total_tokens ||
    previous.requests !== sample.requests ||
    previous.tracked_providers !== sample.tracked_providers
  const next = changed
    ? [...samples, sample].slice(-HISTORY_SAMPLES_PER_PERIOD)
    : samples
  writeUsageHistory([...others, ...next].slice(-HISTORY_SAMPLES_TOTAL))
  return next
}

function useUsageReport(range, reloadToken = 0) {
  const [report, setReport] = useState(() => emptyReport(range.start, range.end))
  const [previousReport, setPreviousReport] = useState(null)
  const [readiness, setReadiness] = useState(EMPTY_READINESS)
  const [history, setHistory] = useState(
    () => readUsageHistory().filter(item => item.period === `${range.start}:${range.end}`),
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    let inFlight = false
    const controller = new AbortController()
    const prevRange = monthRange(addMonths(range.anchor, -1))

    async function load({ silent = false } = {}) {
      if (inFlight || cancelled) return
      inFlight = true
      if (!silent) setLoading(true)
      if (!silent) setError(null)
      try {
        const [currentRes, previousRes, readinessRes] = await Promise.all([
          fetch(`/api/usage/report?start=${range.start}&end=${range.end}`, {signal:controller.signal}),
          fetch(`/api/usage/report?start=${prevRange.start}&end=${prevRange.end}`, {signal:controller.signal}),
          fetch('/api/usage/readiness', {signal:controller.signal}),
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
          setError(null)
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
        inFlight = false
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
      controller.abort()
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [range, reloadToken])

  return { report, previousReport, readiness, history, loading, error }
}

export default function Usage({ compact = false }) {
  const [rangeAnchor, setRangeAnchor] = useState(() => monthRange().anchor)
  const [reloadToken, setReloadToken] = useState(0)
  const [actionState, setActionState] = useState(null)
  const range = useMemo(() => monthRange(rangeAnchor), [rangeAnchor])
  const {report, readiness, loading, error} = useUsageReport(range, reloadToken)

  async function runUsageAction(kind) {
    const action = readiness.actions?.[kind]
    if (!action?.url || actionState?.status === 'running') return
    setActionState({status:'running',kind})
    try {
      const response = await fetch(action.url, {method:action.method || 'POST'})
      const payload = await response.json().catch(()=>({}))
      if (!response.ok) throw new Error(payload.detail || payload.message || 'Action could not be completed.')
      setActionState({status:'success',kind,message:payload.message || 'Action accepted'})
      setReloadToken(value=>value+1)
    } catch (err) {
      setActionState({status:'error',kind,message:err.message})
    }
  }
  return <UsageView compact={compact} report={report} readiness={readiness} loading={loading} error={error}
    range={range} onPrevious={()=>setRangeAnchor(value=>addMonths(value,-1))}
    onNext={()=>setRangeAnchor(value=>addMonths(value,1))} onRefresh={()=>setReloadToken(value=>value+1)}
    actionState={actionState} onAction={runUsageAction}/>
}
