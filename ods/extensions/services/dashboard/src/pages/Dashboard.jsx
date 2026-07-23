import {
  Activity,
  Cpu,
  HardDrive,
  Thermometer,
  Power,
  Zap,
  Clock,
  Brain,
  Brackets,
  MessageSquare,
  Mic,
  FileText,
  Workflow,
  Image,
  Code,
  ChevronRight,
  ChevronDown,
  CircleHelp,
  MoreHorizontal,
  Search,
  Check,
  X,
} from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

// Helper to build external service URLs from current host
const getExternalUrl = (port, path = '') => {
  const suffix = path && path !== '/' ? path : ''
  return typeof window !== 'undefined'
    ? `http://${window.location.hostname}:${port}${suffix}`
    : `http://localhost:${port}${suffix}`
}

// Compute overall health from services (excludes not_deployed from counts)
function computeHealth(services) {
  if (!services?.length) return { text: 'Waiting for telemetry...', color: 'text-theme-text-secondary' }
  const deployed = services.filter(s => s.status !== 'not_deployed')
  if (!deployed.length) return { text: 'No services deployed', color: 'text-theme-text-secondary' }
  const hasRequiredMetadata = deployed.some(s => typeof s.required === 'boolean')
  const scoped = hasRequiredMetadata ? deployed.filter(s => s.required) : deployed
  if (!scoped.length) return { text: 'Optional services only.', color: 'text-theme-text-secondary' }
  const healthy = scoped.filter(s => s.status === 'healthy').length
  const label = hasRequiredMetadata ? 'core services' : 'services'
  return { text: `${healthy}/${scoped.length} ${label} online.`, color: healthy === scoped.length ? 'text-green-400' : 'text-theme-text-secondary' }
}

const FEATURE_ICONS = {
  MessageSquare,
  Mic,
  FileText,
  Workflow,
  Image,
  Code,
}

const SERVICE_LINK_ALIASES = {
  'open-webui': ['open-webui', 'open webui'],
  'hermes-proxy': ['hermes-proxy', 'hermes auth proxy', 'hermes single sign-on', 'hermes sso'],
  'ods-proxy': ['ods-proxy', 'ods server web', 'ods web proxy'],
}

const FEATURE_LAUNCH_FALLBACKS = {
  chat: { type: 'service', service: 'open-webui' },
  voice: { type: 'service', service: 'open-webui' },
  documents: { type: 'service', service: 'open-webui' },
  'hermes-agent': { type: 'service', service: 'hermes-proxy' },
  'hermes-sso': { type: 'internal', path: '/invites' },
  images: { type: 'service', service: 'comfyui' },
  workflows: { type: 'service', service: 'n8n' },
  coding: { type: 'service', service: 'opencode' },
  observability: { type: 'service', service: 'langfuse' },
  'lan-web': { type: 'service', service: 'ods-proxy' },
  'remote-access': { type: 'none' },
  'agent-governance': { type: 'none' },
  'brave-web-search': { type: 'none' },
}

const NON_USER_FACING_LINK_SERVICES = new Set([
  'dashboard-api',
  'embeddings',
  'hermes',
  'litellm',
  'llama-server',
  'privacy-shield',
  'qdrant',
  'searxng',
  'token-spy',
  'tts',
  'whisper',
])

function serviceMatchesTarget(service, target) {
  const targetKey = normalizeServiceKey(target)
  if (!targetKey) return false
  const serviceKeys = [service?.id, service?.name].map(normalizeServiceKey)
  if (serviceKeys.includes(targetKey)) return true

  const aliases = SERVICE_LINK_ALIASES[targetKey] || []
  return aliases.some(alias => serviceKeys.includes(normalizeServiceKey(alias)))
}

function findHealthyService(services, serviceId) {
  return (services || []).find(service =>
    service?.status === 'healthy' &&
    service?.port &&
    serviceMatchesTarget(service, serviceId)
  )
}

function pickFeatureLink(feature, services) {
  const featureKey = normalizeServiceKey(feature?.id)
  const launch = feature?.launch || FEATURE_LAUNCH_FALLBACKS[featureKey]
  if (launch?.type === 'none') return null
  if (launch?.type === 'internal') return launch.path || null
  if (launch?.type === 'service') {
    const launchService = findHealthyService(services, launch.service)
    return launchService ? getExternalUrl(launchService.port, launch.path) : null
  }

  const req = feature?.requirements || {}
  const enabledWanted = [
    ...(feature?.enabledServicesAll || []),
    ...(feature?.enabledServicesAny || []),
  ]
  const requirementWanted = [
    ...(req.servicesAll || req.services || []),
    ...(req.servicesAny || req.services_any || []),
  ]
  const wanted = enabledWanted.length ? enabledWanted : requirementWanted
  const firstHealthy = wanted
    .filter(serviceId => !NON_USER_FACING_LINK_SERVICES.has(normalizeServiceKey(serviceId)))
    .map(serviceId => findHealthyService(services, serviceId))
    .find(Boolean)
  return firstHealthy ? getExternalUrl(firstHealthy.port) : null
}

function normalizeFeatureStatus(featureStatus) {
  switch (featureStatus) {
    case 'enabled':
      return 'ready'
    case 'available':
      return 'ready'
    case 'services_needed':
    case 'insufficient_vram':
      return 'disabled'
    default:
      return 'disabled'
  }
}

// Format large token counts: 1234 → "1.2k", 1500000 → "1.5M", 1500000000 → "1.5B"
function formatTokenCount(n) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return `${n}`
}

// Format uptime: 90061 → "1d 1h 1m"
function formatUptime(seconds) {
  if (!seconds) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

const OVERVIEW_HISTORY_KEY = 'ods-system-overview-history-v1'
const SERVICE_CPU_HISTORY_KEY = 'ods-service-cpu-history-v1'
const OVERVIEW_MAX_SAMPLES = 720
const SERVICE_CPU_MAX_SAMPLES = 80
const OVERVIEW_RANGES = [
  { key: '1H', label: '1H', ms: 60 * 60 * 1000, compareMs: 5 * 60 * 1000, deltaLabel: '5m ago' },
  { key: '6H', label: '6H', ms: 6 * 60 * 60 * 1000, compareMs: 60 * 60 * 1000, deltaLabel: '1h ago' },
  { key: '24H', label: '24H', ms: 24 * 60 * 60 * 1000, compareMs: 6 * 60 * 60 * 1000, deltaLabel: '6h ago' },
  { key: '7D', label: '7D', ms: 7 * 24 * 60 * 60 * 1000, compareMs: 24 * 60 * 60 * 1000, deltaLabel: '1d ago' },
]
const SERVICE_TABS = [
  { key: 'all', label: 'All' },
  { key: 'online', label: 'Online' },
  { key: 'degraded', label: 'Degraded' },
  { key: 'inactive', label: 'Inactive' },
]
const TECH_PANEL_STYLE = {
  background: 'var(--tech-panel-fill)',
  borderColor: 'var(--tech-panel-border)',
  boxShadow: 'var(--tech-panel-shadow)',
}
const TECH_TILE_STYLE = {
  background: 'var(--tech-tile-fill)',
  borderColor: 'var(--tech-tile-border)',
  boxShadow: 'var(--tech-tile-shadow)',
}
let overviewMemoryHistory = []
let serviceCpuMemoryHistory = {}

const SERVICE_DESCRIPTIONS = {
  ape: 'Policy management and enforcement',
  dashboard: 'Central dashboard and web UI',
  'dashboard-api': 'System metrics and status API',
  litellm: 'LLM routing and load balancing',
  'llama-server': 'Local model inference runtime',
  'open-webui': 'Chat interface for local models',
  perplexica: 'Deep research and search interface',
  searxng: 'Private metasearch engine',
  'privacy-shield': 'PII detection and privacy filtering',
  'token-spy': 'Usage telemetry and token tracking',
  opencode: 'Browser-based coding assistant',
  qdrant: 'Vector database for retrieval',
  whisper: 'Speech-to-text service',
  kokoro: 'Text-to-speech service',
  comfyui: 'Image generation workflow UI',
  n8n: 'Workflow automation engine',
}

function normalizeMetricNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : 0
}

function normalizeServiceKey(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/\([^)]*\)/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function getServiceDescription(id, name) {
  const key = normalizeServiceKey(id || name)
  return SERVICE_DESCRIPTIONS[id] || SERVICE_DESCRIPTIONS[key] || 'ODS service'
}

function getStatusTone(status) {
  switch (status) {
    case 'healthy':
      return {
        label: 'Online',
        dot: 'bg-emerald-400',
        text: 'text-emerald-300',
        pill: 'border-emerald-400/25 bg-emerald-400/[0.08]',
      }
    case 'restarting':
      return {
        label: 'Restarting',
        dot: 'bg-amber-400',
        text: 'text-amber-300',
        pill: 'border-amber-400/25 bg-amber-400/[0.08]',
      }
    case 'degraded':
      return {
        label: 'Degraded',
        dot: 'bg-amber-400',
        text: 'text-amber-300',
        pill: 'border-amber-400/25 bg-amber-400/[0.08]',
      }
    default:
      return {
        label: 'Inactive',
        dot: 'bg-red-400',
        text: 'text-red-300',
        pill: 'border-red-400/25 bg-red-400/[0.08]',
      }
  }
}

function getServiceTabStatus(status) {
  if (status === 'healthy') return 'online'
  if (status === 'degraded') return 'degraded'
  return 'inactive'
}

function LlmSwapPill({ llm }) {
  if (!llm?.consumes) return null
  const safe = llm.swap_safe === true
  const Icon = safe ? Check : X
  const label = safe ? 'Swap-safe' : 'Not swap-safe'
  const tone = safe
    ? 'border-green-500/20 bg-green-500/10 text-green-300'
    : 'border-red-500/20 bg-red-500/10 text-red-300'

  return (
    <span
      className={`shrink-0 inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[8px] font-semibold ${tone}`}
      title={llm.swap_safe_reason || label}
    >
      <Icon size={9} />
      {label}
    </span>
  )
}

function formatRamMb(value) {
  if (value == null) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (n >= 1024) return `${(n / 1024).toFixed(n >= 10240 ? 0 : 1)} GB`
  return `${Math.round(n)} MB`
}

function formatCpuPercent(value) {
  if (value == null) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n.toFixed(1)}%`
}

function readOverviewHistory() {
  try {
    const raw = globalThis.localStorage?.getItem(OVERVIEW_HISTORY_KEY)
    if (!raw) return overviewMemoryHistory
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return overviewMemoryHistory
    return parsed.filter(sample =>
      Number.isFinite(sample?.t) &&
      Number.isFinite(sample?.tokensPerSecond) &&
      Number.isFinite(sample?.totalTokens) &&
      (!sample?.tokenCountMode || typeof sample.tokenCountMode === 'string')
    )
  } catch {
    return overviewMemoryHistory
  }
}

function writeOverviewHistory(samples) {
  overviewMemoryHistory = samples
  try {
    globalThis.localStorage?.setItem(OVERVIEW_HISTORY_KEY, JSON.stringify(samples))
  } catch {
    // Memory history keeps the tabs usable when storage is blocked.
  }
}

function readServiceCpuHistory() {
  try {
    const raw = globalThis.localStorage?.getItem(SERVICE_CPU_HISTORY_KEY)
    if (!raw) return serviceCpuMemoryHistory
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return serviceCpuMemoryHistory
    return Object.fromEntries(
      Object.entries(parsed).map(([id, samples]) => [
        id,
        Array.isArray(samples)
          ? samples.filter(sample => Number.isFinite(sample?.t) && Number.isFinite(sample?.cpu))
          : [],
      ])
    )
  } catch {
    return serviceCpuMemoryHistory
  }
}

function writeServiceCpuHistory(history) {
  serviceCpuMemoryHistory = history
  try {
    globalThis.localStorage?.setItem(SERVICE_CPU_HISTORY_KEY, JSON.stringify(history))
  } catch {
    // Memory history keeps the sparkline usable when storage is blocked.
  }
}

function useOverviewHistory(tokensPerSecond, totalTokens, tokenCountMode = 'unavailable') {
  const [samples, setSamples] = useState(() => readOverviewHistory().filter(sample =>
    (sample.tokenCountMode || 'cumulative') === tokenCountMode
  ))

  useEffect(() => {
    const now = Date.now()
    const nextSample = {
      t: now,
      tokensPerSecond: normalizeMetricNumber(tokensPerSecond),
      totalTokens: normalizeMetricNumber(totalTokens),
      tokenCountMode,
    }

    setSamples(current => {
      const cutoff = now - OVERVIEW_RANGES[OVERVIEW_RANGES.length - 1].ms
      const recent = current.filter(sample =>
        sample.t >= cutoff &&
        (sample.tokenCountMode || 'cumulative') === tokenCountMode
      )
      const last = recent[recent.length - 1]
      if (
        last &&
        now - last.t < 3000 &&
        last.tokensPerSecond === nextSample.tokensPerSecond &&
        last.totalTokens === nextSample.totalTokens
      ) {
        return current
      }

      const next = [...recent, nextSample].slice(-OVERVIEW_MAX_SAMPLES)
      writeOverviewHistory(next)
      return next
    })
  }, [tokensPerSecond, totalTokens, tokenCountMode])

  return samples
}

function useServiceCpuHistory(services) {
  const [history, setHistory] = useState(() => readServiceCpuHistory())

  useEffect(() => {
    const now = Date.now()
    const cutoff = now - 60 * 60 * 1000
    const rowsWithCpu = services.filter(service => Number.isFinite(service.cpuPercent))
    if (!rowsWithCpu.length) return

    setHistory(current => {
      const next = { ...current }
      let changed = false

      rowsWithCpu.forEach(service => {
        const currentSamples = (next[service.id] || []).filter(sample => sample.t >= cutoff)
        const last = currentSamples[currentSamples.length - 1]
        if (last && now - last.t < 3000 && last.cpu === service.cpuPercent) {
          next[service.id] = currentSamples
          return
        }

        next[service.id] = [
          ...currentSamples,
          { t: now, cpu: service.cpuPercent },
        ].slice(-SERVICE_CPU_MAX_SAMPLES)
        changed = true
      })

      Object.keys(next).forEach(serviceId => {
        const pruned = (next[serviceId] || []).filter(sample => sample.t >= cutoff)
        if (pruned.length !== next[serviceId].length) {
          next[serviceId] = pruned
          changed = true
        }
      })

      if (!changed) return current
      writeServiceCpuHistory(next)
      return next
    })
  }, [services])

  return history
}

function buildServiceRows(statusServices, resourceServices) {
  const resources = resourceServices || []
  const resourcesByName = new Map(resources.map(resource => [normalizeServiceKey(resource.name), resource]))
  const seen = new Set()
  const rows = []

  ;(statusServices || [])
    .filter(service => service.status !== 'not_deployed')
    .forEach((service, index) => {
      const resource = resourcesByName.get(normalizeServiceKey(service.name))
      const id = resource?.id || normalizeServiceKey(service.name) || `service-${index}`
      const hasSemantics = typeof service.required === 'boolean' || service.impact || service.category
      seen.add(id)
      rows.push({
        id,
        name: service.name || resource?.name || id,
        description: getServiceDescription(id, service.name || resource?.name),
        status: service.status || 'unknown',
        category: service.category || null,
        required: service.required === true,
        hasSemantics,
        impact: service.impact || (hasSemantics ? (service.required ? 'core' : 'optional') : null),
        state: service.state || null,
        severity: service.severity || null,
        countsAsIssue: service.countsAsIssue === true,
        llm: service.llm || null,
        port: service.port,
        uptime: service.uptime,
        cpuPercent: Number.isFinite(resource?.container?.cpu_percent) ? resource.container.cpu_percent : null,
        memoryUsedMb: Number.isFinite(resource?.container?.memory_used_mb) ? resource.container.memory_used_mb : null,
        memoryLimitMb: Number.isFinite(resource?.container?.memory_limit_mb) ? resource.container.memory_limit_mb : null,
        pids: Number.isFinite(resource?.container?.pids) ? resource.container.pids : null,
        containerName: resource?.container?.container_name || null,
        type: resource?.type || 'unknown',
        restartable: resource?.restartable === true,
        restartUnavailableReason: resource?.restart_unavailable_reason || null,
        disk: resource?.disk || null,
      })
    })

  resources.forEach((resource, index) => {
    const id = resource.id || normalizeServiceKey(resource.name) || `resource-${index}`
    if (seen.has(id)) return
    const hasSemantics = typeof resource.required === 'boolean' || resource.impact || resource.category
    rows.push({
      id,
      name: resource.name || id,
      description: getServiceDescription(id, resource.name),
      status: 'unknown',
      category: resource.category || null,
      required: resource.required === true,
      hasSemantics,
      impact: resource.impact || (hasSemantics ? (resource.required ? 'core' : 'optional') : null),
      state: resource.state || null,
      severity: resource.severity || null,
      countsAsIssue: resource.countsAsIssue === true,
      llm: resource.llm || null,
      port: null,
      uptime: null,
      cpuPercent: Number.isFinite(resource?.container?.cpu_percent) ? resource.container.cpu_percent : null,
      memoryUsedMb: Number.isFinite(resource?.container?.memory_used_mb) ? resource.container.memory_used_mb : null,
      memoryLimitMb: Number.isFinite(resource?.container?.memory_limit_mb) ? resource.container.memory_limit_mb : null,
      pids: Number.isFinite(resource?.container?.pids) ? resource.container.pids : null,
      containerName: resource?.container?.container_name || null,
      type: resource?.type || 'unknown',
      restartable: resource?.restartable === true,
      restartUnavailableReason: resource?.restart_unavailable_reason || null,
      disk: resource?.disk || null,
    })
  })

  return rows
}

function buildSignalPath(points) {
  if (!points.length) return ''
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`

  let path = `M ${points[0].x} ${points[0].y}`

  for (let i = 1; i < points.length - 1; i += 1) {
    const xc = (points[i].x + points[i + 1].x) / 2
    const yc = (points[i].y + points[i + 1].y) / 2
    path += ` Q ${points[i].x} ${points[i].y} ${xc} ${yc}`
  }

  const penultimate = points[points.length - 2]
  const last = points[points.length - 1]
  path += ` Q ${penultimate.x} ${penultimate.y} ${last.x} ${last.y}`

  return path
}

function reduceSamples(samples, maxPoints = 16) {
  if (samples.length <= maxPoints) return samples
  const step = (samples.length - 1) / (maxPoints - 1)
  return Array.from({ length: maxPoints }, (_, index) => samples[Math.round(index * step)])
}

function buildOverviewSeries(samples, range, field) {
  const now = Date.now()
  const filtered = reduceSamples(samples.filter(sample => sample.t >= now - range.ms))
  return {
    values: filtered.map(sample => sample[field]),
    timestamps: filtered.map(sample => sample.t),
  }
}

function formatClockLabel(timestamp, rangeKey) {
  const date = new Date(timestamp)
  if (rangeKey === '7D') {
    return date.toLocaleDateString(undefined, { weekday: 'short' })
  }

  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${hours}:${minutes}`
}

function buildTimeLabels(timestamps, rangeKey) {
  if (!timestamps.length) return []
  const indexes = [0, 0.25, 0.5, 0.75, 1].map(progress =>
    Math.min(timestamps.length - 1, Math.round(progress * (timestamps.length - 1)))
  )
  return indexes.map(index => formatClockLabel(timestamps[index], rangeKey))
}

function computeDeltaFromSamples(samples, range, field, currentValue) {
  const now = Date.now()
  const scoped = samples
    .filter(sample => sample.t >= now - range.ms && Number.isFinite(sample[field]))
    .sort((a, b) => a.t - b.t)
  if (scoped.length < 2) return null

  const current = normalizeMetricNumber(currentValue)
  const targetTime = now - range.compareMs
  const historical = scoped.filter(sample => sample.t <= targetTime)
  const baseSample = historical[historical.length - 1] || scoped[0]
  const base = baseSample?.[field]
  if (!base) return null
  return ((current - base) / Math.abs(base)) * 100
}

function buildChartPoints(values, maxValue) {
  const width = 460
  const height = 190
  const paddingLeft = 38
  const paddingRight = 12
  const paddingTop = 26
  const paddingBottom = 30
  const usableWidth = width - paddingLeft - paddingRight
  const usableHeight = height - paddingTop - paddingBottom

  return values.map((value, index) => {
    const ratio = clamp(maxValue > 0 ? value / maxValue : 0, 0.08, 0.94)
    return {
      x: paddingLeft + (usableWidth / Math.max(values.length - 1, 1)) * index,
      y: height - paddingBottom - ratio * usableHeight,
    }
  })
}

export default function Dashboard({ status, loading }) {
  const [featuresData, setFeaturesData] = useState(null)
  const [serviceResources, setServiceResources] = useState(null)

  useEffect(() => {
    let mounted = true

    const fetchFeatures = async () => {
      try {
        const res = await fetch('/api/features')
        if (!res.ok) return
        const data = await res.json()
        if (mounted) setFeaturesData(data)
      } catch {
        // Feature cards degrade gracefully to status-only view when API fails.
      }
    }

    fetchFeatures()
    // Skip ticks while the tab is hidden; refresh immediately on return (#1490)
    const tick = () => { if (!document.hidden) fetchFeatures() }
    const timer = setInterval(tick, 15000)
    const onVisibility = () => { if (!document.hidden) fetchFeatures() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      mounted = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  useEffect(() => {
    let mounted = true

    const fetchServiceResources = async () => {
      try {
        const res = await fetch('/api/services/resources')
        if (!res.ok) return
        const data = await res.json()
        if (mounted) setServiceResources(data)
      } catch {
        // Service rows keep rendering status data when per-container metrics are unavailable.
      }
    }

    fetchServiceResources()
    // /api/services/resources runs `docker stats` + /data disk walks
    // server-side (TTL-cached 20s/60s) — the most expensive poller on this
    // page. Skip ticks while the tab is hidden; refresh on return (#1490).
    const tick = () => { if (!document.hidden) fetchServiceResources() }
    const timer = setInterval(tick, 10000)
    const onVisibility = () => { if (!document.hidden) fetchServiceResources() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      mounted = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  // All hooks must be called before any conditional returns (React rules of hooks)
  const features = useMemo(() => {
    if (featuresData?.features?.length) {
      return [...featuresData.features].sort((a, b) => (a.priority || 999) - (b.priority || 999))
    }
    return []
  }, [featuresData])
  const serviceRows = useMemo(
    () => buildServiceRows(status?.services, serviceResources?.services),
    [status?.services, serviceResources?.services]
  )

  if (loading) {
    return (
      <div className="p-8 animate-pulse">
        <div className="h-8 bg-theme-card rounded w-1/3 mb-4" />
        <p className="text-sm text-theme-text-muted mb-8">Linking modules... reading telemetry...</p>
        <div className="grid grid-cols-3 gap-6">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-40 bg-theme-card rounded-xl" />
          ))}
        </div>
      </div>
    )
  }

  const health = computeHealth(status?.services)
  const systemMetrics = []

  if (status?.gpu) {
    if (status.gpu.memoryType === 'unified') {
      // Apple Silicon: GPU utilization isn't available (always 0), show chip info instead.
      systemMetrics.push({
        icon: Zap,
        label: 'Chip',
        value: status.gpu.name.replace('Apple ', ''),
        subvalue: 'Apple Silicon',
      })
      if (status?.ram) {
        systemMetrics.push({
          icon: HardDrive,
          label: 'Mem Used',
          value: `${status.ram.used_gb} GB`,
          subvalue: `of ${status.ram.total_gb} GB unified`,
          percent: status.ram.percent,
        })
      }
    } else {
      const hasLiveUtilization = Number.isFinite(status.gpu.utilization)
      const hasLiveVramUsage = Number.isFinite(status.gpu.vramUsed)
      systemMetrics.push({
        icon: Activity,
        label: 'GPU',
        value: hasLiveUtilization ? `${status.gpu.utilization}%` : '—',
        subvalue: status.gpu.name.replace('NVIDIA ', '').replace('AMD ', ''),
        percent: hasLiveUtilization ? status.gpu.utilization : undefined,
      })
      systemMetrics.push({
        icon: HardDrive,
        label: 'VRAM',
        value: hasLiveVramUsage ? `${status.gpu.vramUsed.toFixed(1)} GB` : '—',
        subvalue: `of ${status.gpu.vramTotal} GB`,
        percent: hasLiveVramUsage && status.gpu.vramTotal > 0
          ? (status.gpu.vramUsed / status.gpu.vramTotal) * 100
          : undefined,
      })
    }
  }

  if (status?.cpu) {
    systemMetrics.push({
      icon: Cpu,
      label: 'CPU',
      value: `${status.cpu.percent}%`,
      subvalue: 'utilization',
      percent: status.cpu.percent,
    })
  }

  if (status?.ram && status?.gpu?.memoryType !== 'unified') {
    systemMetrics.push({
      icon: HardDrive,
      label: 'RAM',
      value: `${status.ram.used_gb} GB`,
      subvalue: `of ${status.ram.total_gb} GB`,
      percent: status.ram.percent,
    })
  }

  if (status?.gpu?.powerDraw != null) {
    systemMetrics.push({
      icon: Power,
      label: 'GPU Power',
      value: `${status.gpu.powerDraw}W`,
      subvalue: 'live',
    })
  }

  if (status?.gpu?.memoryType !== 'unified') {
    systemMetrics.push({
      icon: Thermometer,
      label: 'GPU Temp',
      value: status?.gpu?.temperature != null ? `${status.gpu.temperature}°C` : '—',
      subvalue: status?.gpu?.temperature != null
        ? status.gpu.temperature < 70 ? 'normal' : status.gpu.temperature < 85 ? 'warm' : 'hot'
        : 'thermal',
      alert: status?.gpu?.temperature >= 85,
    })
  }

  systemMetrics.push(
    {
      icon: Brackets,
      label: 'Context',
      value: status?.inference?.contextSize ? `${(status.inference.contextSize / 1024).toFixed(0)}k` : '—',
      subvalue: 'max tokens',
    },
    {
      icon: Clock,
      label: 'Uptime',
      value: formatUptime(status?.uptime || 0),
      subvalue: 'system',
    },
    {
      icon: Brain,
      label: 'Model',
      value: status?.inference?.loadedModel || '—',
      subvalue: 'loaded',
    }
  )

  return (
    <div className="p-8">
      {/* Header with live meta strip */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-theme-text">Dashboard</h1>
          <p className={`mt-1 ${health.color}`}>
            {health.text}
          </p>
        </div>
        <div
          className="liquid-metal-frame liquid-metal-frame--soft flex items-center gap-4 rounded-lg border px-3 py-2 font-mono text-xs text-theme-text-muted"
          style={TECH_TILE_STYLE}
        >
          {status?.tier && <span className="text-theme-accent-light">{status.tier}</span>}
          {status?.model?.name && <span>{status.model.name}</span>}
          {status?.version && <span>v{status.version}</span>}
        </div>
      </div>

      {/* Feature Cards */}
      <div className="liquid-metal-sequence-grid liquid-metal-sequence-grid--features grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2.5 mb-10">
        {features.length > 0 ? (
          features.map(feature => (
            <FeatureCard
              key={feature.id}
              icon={FEATURE_ICONS[feature.icon] || MessageSquare}
              title={feature.name}
              description={feature.description}
              href={pickFeatureLink(feature, status?.services)}
              status={normalizeFeatureStatus(feature.status)}
              hint={
                feature.status === 'services_needed'
                  ? `Needs services: ${(feature.requirements?.servicesMissing || []).join(', ')}`
                  : feature.status === 'insufficient_vram'
                    ? `Needs ${feature.requirements?.vramGb || 0}GB VRAM`
                    : undefined
              }
            />
          ))
        ) : (
          <FeatureCard
            icon={MessageSquare}
            title="AI Chat"
            description="Feature metadata is loading..."
            href={null}
            status="disabled"
            hint="Waiting for /api/features"
          />
        )}
      </div>

      {/* Multi-GPU summary strip — only shown when gpu_count > 1 */}
      {status?.gpu?.gpu_count > 1 && (
        <Link to="/gpu" className="block mb-6">
          <div className="liquid-metal-frame flex items-center justify-between p-4 bg-indigo-500/10 border border-indigo-500/25 rounded-xl transition-colors group">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-indigo-500/15 rounded-lg">
                <Activity size={18} className="text-indigo-400" />
              </div>
              <div>
                <p className="text-sm font-semibold text-white">
                  Multi-GPU System · {status.gpu.gpu_count} GPUs
                </p>
                <p className="text-xs text-zinc-400 mt-0.5">
                  {status.gpu.name} · {status.gpu.utilization}% avg util · {status.gpu.vramUsed?.toFixed(1)}/{status.gpu.vramTotal} GB VRAM
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1 text-xs text-indigo-400 group-hover:text-indigo-300 transition-colors font-medium">
              GPU Monitor
              <ChevronRight size={14} />
            </div>
          </div>
        </Link>
      )}

      {/* System Overview */}
      <div className="mb-10 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.28fr)_minmax(320px,0.72fr)]">
        <SystemOverviewPanel
          tokensPerSecond={status?.inference?.tokensPerSecond || 0}
          totalTokens={status?.inference?.lifetimeTokens || 0}
          tokenCountMode={status?.inference
            ? status.inference.tokenCountMode || 'cumulative'
            : 'unavailable'}
        />
        <SystemMetricsPanel metrics={systemMetrics} />
      </div>

      <ServicesPanel services={serviceRows} />

      {/* Feature Discovery is already shown at the top */}
    </div>
  )
}


const FeatureCard = memo(function FeatureCard({ icon: Icon, title, description, href, status, hint }) {
  const isExternal = href?.startsWith('http')
  const isInteractive = status !== 'disabled' && status !== 'coming' && Boolean(href)
  const statusColors = {
    ready: 'hover:border-theme-accent/30',
    disabled: 'opacity-60',
    coming: 'opacity-30'
  }
  const statusMeta = {
    ready: {
      label: 'Ready',
      dotClass: 'bg-emerald-400',
      textClass: 'text-theme-text-secondary'
    },
    coming: {
      label: 'Coming soon',
      dotClass: 'bg-theme-text-muted/45',
      textClass: 'text-theme-text-muted'
    }
  }
  const detailText = hint ? `${description} ${hint}` : description

  const content = (
    <div
      className={`feature-card-compact liquid-metal-frame liquid-metal-sequence-card group h-full min-h-[56px] px-2.5 py-2 rounded-xl border ${statusColors[status]} transition-all ${isInteractive ? 'cursor-pointer hover:shadow-md' : 'cursor-default'} flex items-center justify-between gap-2`}
      style={{ ...TECH_TILE_STYLE, overflow: 'visible' }}
    >
      <div className="min-w-0 flex items-center gap-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-theme-border bg-theme-bg/45">
          <Icon size={16} className="text-theme-text-secondary" />
        </div>

        <div className="min-w-0 flex items-center gap-1.5">
          <h3 className="truncate text-sm font-semibold text-theme-text">
            {title}
          </h3>

          {statusMeta[status] && (
            <span className={`shrink-0 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[8px] font-medium uppercase tracking-[0.12em] ${statusMeta[status].textClass}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${statusMeta[status].dotClass}`} />
              {statusMeta[status].label}
            </span>
          )}
        </div>
      </div>

      <div className="relative shrink-0 group/info" title={detailText}>
        <div className="flex h-6.5 w-6.5 items-center justify-center rounded-full border border-theme-border bg-theme-bg/45 text-theme-text-muted/75 transition-colors group-hover:border-theme-accent/20 group-hover:text-theme-text-secondary">
          <CircleHelp size={13} />
        </div>

        <div className="pointer-events-none absolute bottom-[calc(100%+0.45rem)] right-0 z-20 w-52 translate-y-1 rounded-lg border border-theme-border bg-theme-card/95 px-3 py-2 text-[11px] leading-4 text-theme-text-muted opacity-0 shadow-2xl transition-all duration-150 group-hover/info:translate-y-0 group-hover/info:opacity-100">
          {description}
          {status === 'disabled' && hint && (
            <p className="mt-2 font-mono text-[10px] text-theme-text-secondary">{hint}</p>
          )}
        </div>
      </div>
    </div>
  )

  if (status === 'disabled' || status === 'coming' || !href) {
    return <div className="block h-full liquid-metal-sequence-slot">{content}</div>
  }

  if (isExternal) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="block h-full liquid-metal-sequence-slot">
        {content}
      </a>
    )
  }

  return <Link to={href} className="block h-full liquid-metal-sequence-slot">{content}</Link>
})

const SystemOverviewPanel = memo(function SystemOverviewPanel({ tokensPerSecond, totalTokens, tokenCountMode }) {
  const [rangeKey, setRangeKey] = useState('1H')
  const range = OVERVIEW_RANGES.find(item => item.key === rangeKey) || OVERVIEW_RANGES[0]
  const history = useOverviewHistory(tokensPerSecond, totalTokens, tokenCountMode)
  const throughput = useMemo(
    () => buildOverviewSeries(history, range, 'tokensPerSecond'),
    [history, range]
  )
  const generated = useMemo(
    () => buildOverviewSeries(history, range, 'totalTokens'),
    [history, range]
  )

  return (
    <section
      className="h-full rounded-xl border px-4 py-4 sm:px-5 sm:py-5"
      style={TECH_PANEL_STYLE}
    >
      <div className="mb-4 flex items-center justify-between gap-4">
        <h2 className="text-base font-semibold text-theme-text sm:text-lg">System Overview</h2>
        <div className="flex h-7 shrink-0 items-center rounded-md border border-theme-border bg-theme-bg/35 p-0.5">
          {OVERVIEW_RANGES.map(item => (
            <button
              key={item.key}
              type="button"
              onClick={() => setRangeKey(item.key)}
              className={`h-6 min-w-10 rounded px-2 text-[10px] font-semibold transition-colors ${
                rangeKey === item.key
                  ? 'bg-theme-accent/28 text-theme-accent-light shadow-[0_0_0_1px_rgba(215,164,255,0.22)_inset]'
                  : 'text-theme-text-muted hover:text-theme-text-secondary'
              }`}
              aria-pressed={rangeKey === item.key}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid min-h-[246px] grid-cols-1 gap-5 lg:grid-cols-2 lg:gap-0">
        <OverviewChart
          chartId="tokens-per-second"
          title="TOKENS PER SECOND"
          subtitle="Live Throughput"
          values={throughput.values}
          timestamps={throughput.timestamps}
          range={range}
          rangeKey={rangeKey}
          currentDisplay={(tokensPerSecond || 0).toFixed(1)}
          unit="tokens / sec"
          delta={computeDeltaFromSamples(history, range, 'tokensPerSecond', tokensPerSecond)}
          accent="rgba(168,85,247,0.98)"
          fill="rgba(157,0,255,0.52)"
          defaultMax={12}
          axisFormatter={(value) => `${Math.round(value)}`}
        />
        <OverviewChart
          chartId="tokens-generated"
          title={tokenCountMode === 'latest_completion' ? 'OUTPUT TOKENS' : 'TOKENS GENERATED'}
          subtitle={tokenCountMode === 'latest_completion'
            ? 'Latest Completion'
            : tokenCountMode === 'cumulative'
              ? 'Accumulated Output'
              : 'Waiting for telemetry'}
          values={generated.values}
          timestamps={generated.timestamps}
          range={range}
          rangeKey={rangeKey}
          currentDisplay={formatTokenCount(totalTokens || 0)}
          unit="tokens"
          delta={computeDeltaFromSamples(history, range, 'totalTokens', totalTokens)}
          accent="rgba(251,146,60,0.98)"
          fill="rgba(245,158,11,0.48)"
          defaultMax={6000}
          axisFormatter={(value) => formatTokenCount(Math.round(value))}
          divided
        />
      </div>
    </section>
  )
})

const OverviewChart = memo(function OverviewChart({
  chartId,
  title,
  subtitle,
  values,
  timestamps,
  range,
  rangeKey,
  currentDisplay,
  unit,
  delta,
  accent,
  fill,
  defaultMax,
  axisFormatter,
  divided = false,
}) {
  const maxValue = Math.max(...values, defaultMax) * 1.08
  const points = buildChartPoints(values, maxValue)
  const hasSeries = points.length >= 2
  const path = hasSeries ? buildSignalPath(points) : ''
  const baseline = 160
  const firstPoint = points[0] || { x: 38, y: baseline }
  const lastPoint = points[points.length - 1] || firstPoint
  const areaPath = path
    ? `${path} L ${lastPoint.x} ${baseline} L ${firstPoint.x} ${baseline} Z`
    : ''
  const yLabels = [maxValue, maxValue * 0.66, maxValue * 0.33, 0]
  const timeLabels = buildTimeLabels(timestamps, rangeKey)
  const deltaPrefix = delta == null || delta >= 0 ? '↑' : '↓'
  const deltaTone = delta != null && delta < 0 ? 'text-red-400' : 'text-emerald-400'

  return (
    <div className={`min-w-0 ${divided ? 'lg:border-l lg:border-theme-border lg:pl-8' : 'lg:pr-8'}`}>
      <div className="mb-3 flex min-h-[64px] flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="min-w-[210px]">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-theme-text-muted/70">
            {title}
          </p>
          <p className="mt-1 text-xs text-theme-text-muted">{subtitle}</p>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="font-mono text-[34px] font-bold leading-none text-theme-text sm:text-[38px]">
              {currentDisplay}
            </span>
            <span className="text-xs text-theme-text-muted">{unit}</span>
          </div>
        </div>

        {delta == null ? (
          <div className="mb-2 whitespace-nowrap text-xs font-medium text-theme-text-muted">
            collecting samples
          </div>
        ) : (
          <div className={`mb-2 whitespace-nowrap text-xs font-semibold ${deltaTone}`}>
            {deltaPrefix} {Math.abs(delta).toFixed(1)}%
            <span className="ml-1 font-normal text-theme-text-muted">vs {range.deltaLabel}</span>
          </div>
        )}
      </div>

      <svg
        viewBox="0 0 460 190"
        preserveAspectRatio="none"
        className="block h-[176px] w-full select-none overflow-visible"
        role="img"
        aria-label={`${title} chart`}
      >
        <defs>
          <linearGradient id={`overview-line-${chartId}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={accent} stopOpacity="0.82" />
            <stop offset="48%" stopColor={accent} stopOpacity="1" />
            <stop offset="100%" stopColor="#ffffff" stopOpacity="0.8" />
          </linearGradient>
          <linearGradient id={`overview-fill-${chartId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={fill} stopOpacity="0.68" />
            <stop offset="58%" stopColor={fill} stopOpacity="0.24" />
            <stop offset="100%" stopColor={fill} stopOpacity="0" />
          </linearGradient>

        </defs>

        {[36, 72, 108, 144, 160].map((y) => (
          <line
            key={`${chartId}-grid-${y}`}
            x1="38"
            x2="448"
            y1={y}
            y2={y}
            stroke="rgba(255,255,255,0.065)"
            strokeWidth="1"
          />
        ))}

        {yLabels.map((label, index) => (
          <text
            key={`${chartId}-axis-${index}`}
            x="0"
            y={[39, 83, 127, 162][index]}
            fill="rgba(255,255,255,0.45)"
            fontSize="10"
            fontWeight="500"
          >
            {axisFormatter(label)}
          </text>
        ))}

        {areaPath && (
          <path
            d={areaPath}
            fill={`url(#overview-fill-${chartId})`}
          />
        )}
        {hasSeries ? (
          <path
            d={path}
            fill="none"
            stroke={`url(#overview-line-${chartId})`}
            strokeWidth="3"
            strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 4px ${accent})` }}
          />
        ) : (
          <text
            x="243"
            y="105"
            textAnchor="middle"
            fill="rgba(255,255,255,0.42)"
            fontSize="11"
            fontWeight="600"
          >
            collecting telemetry
          </text>
        )}

        {timeLabels.map((label, index) => (
          <text
            key={`${chartId}-time-${index}`}
            x={38 + ((448 - 38) / Math.max(timeLabels.length - 1, 1)) * index}
            y="184"
            textAnchor={index === 0 ? 'start' : index === timeLabels.length - 1 ? 'end' : 'middle'}
            fill="rgba(255,255,255,0.42)"
            fontSize="10"
            fontWeight="500"
          >
            {label}
          </text>
        ))}
      </svg>
    </div>
  )
})

const SystemMetricsPanel = memo(function SystemMetricsPanel({ metrics }) {
  return (
    <aside
      className="h-full rounded-xl border px-4 py-4 sm:px-5 sm:py-5"
      style={TECH_PANEL_STYLE}
    >
      <div className="mb-4 flex min-h-7 items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-theme-text sm:text-lg">System Status</h2>
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/65">
          Live Telemetry
        </span>
      </div>

      <div className="liquid-metal-sequence-grid liquid-metal-sequence-grid--system grid grid-cols-2 gap-2">
        {metrics.map((metric) => (
          <MetricCard
            key={metric.label}
            icon={metric.icon}
            label={metric.label}
            value={metric.value}
            subvalue={metric.subvalue}
            percent={metric.percent}
            alert={metric.alert}
            compact
          />
        ))}
      </div>
    </aside>
  )
})

const MetricCard = memo(function MetricCard({ icon: Icon, label, value, subvalue, percent, alert, compact = false }) {
  const progressTone = percent > 90
    ? 'liquid-metal-progress-fill liquid-metal-progress-fill--danger'
    : percent > 70
      ? 'liquid-metal-progress-fill liquid-metal-progress-fill--warn'
      : 'liquid-metal-progress-fill'

  return (
    <div
      className={`liquid-metal-frame liquid-metal-frame--soft liquid-metal-sequence-card min-w-0 rounded-xl border ${compact ? 'px-2.5 py-2' : 'p-4'} overflow-hidden`}
      style={TECH_TILE_STYLE}
    >
      <div className={`flex items-center gap-1.5 ${compact ? 'mb-1' : 'mb-2'}`}>
        <Icon size={compact ? 12 : 13} className={alert ? 'text-red-400' : 'text-theme-text-muted/50'} />
        <span className={`${compact ? 'text-[9px]' : 'text-[9px]'} font-semibold uppercase tracking-[0.13em] text-theme-text-muted/55`}>{label}</span>
      </div>
      <div className={`${compact ? 'text-[20px]' : 'text-[28px]'} font-bold text-theme-text font-mono leading-none truncate`} title={value}>{value}</div>
      <div className={`${compact ? 'text-[10px]' : 'text-[10px]'} text-theme-text-muted/70 mt-0.5 truncate`}>{subvalue}</div>
      {percent !== undefined && (
        <div className={`liquid-metal-progress-track rounded-full ${compact ? 'mt-1.5 h-[2px]' : 'mt-3 h-[4px]'} overflow-hidden`}>
          <div
            className={`h-full rounded-full transition-all ${progressTone}`}
            style={{ width: `${Math.min(percent, 100)}%` }}
          />
        </div>
      )}
    </div>
  )
})

const ServicesPanel = memo(function ServicesPanel({ services }) {
  const [activeTab, setActiveTab] = useState('all')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [openMenuId, setOpenMenuId] = useState(null)
  const [actionState, setActionState] = useState({})
  const panelRef = useRef(null)
  const cpuHistory = useServiceCpuHistory(services)
  useEffect(() => {
    if (!openMenuId) return undefined

    const handlePointerDown = (event) => {
      if (panelRef.current && !panelRef.current.contains(event.target)) {
        setOpenMenuId(null)
      }
    }
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setOpenMenuId(null)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [openMenuId])
  const counts = useMemo(() => {
    const next = { all: services.length, online: 0, degraded: 0, inactive: 0 }
    services.forEach(service => {
      next[getServiceTabStatus(service.status)] += 1
    })
    return next
  }, [services])
  const filteredServices = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return services.filter(service => {
      if (activeTab !== 'all' && getServiceTabStatus(service.status) !== activeTab) return false
      if (!normalizedQuery) return true
      return [
        service.name,
        service.description,
        service.status,
        service.port ? `:${service.port}` : '',
      ].some(value => String(value || '').toLowerCase().includes(normalizedQuery))
    })
  }, [activeTab, query, services])
  const visibleServices = expanded ? filteredServices : filteredServices.slice(0, 4)
  const hiddenCount = Math.max(filteredServices.length - visibleServices.length, 0)

  return (
    <section
      ref={panelRef}
      className="mb-12 rounded-xl border px-3 py-3 sm:px-4 sm:py-4"
      style={TECH_PANEL_STYLE}
    >
      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <h2 className="text-lg font-semibold text-theme-text">Services</h2>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="flex min-h-8 items-center gap-1 border-b border-theme-border sm:border-b-0">
            {SERVICE_TABS.map(tab => (
              <button
                key={tab.key}
                type="button"
                onClick={() => {
                  setActiveTab(tab.key)
                  setExpanded(false)
                  setOpenMenuId(null)
                }}
                className={`relative h-8 px-2.5 text-[10px] font-semibold transition-colors ${
                  activeTab === tab.key
                    ? 'text-theme-accent-light'
                    : 'text-theme-text-muted hover:text-theme-text-secondary'
                }`}
                aria-pressed={activeTab === tab.key}
              >
                {tab.label} ({counts[tab.key]})
                {activeTab === tab.key && (
                  <span className="absolute inset-x-1 -bottom-px h-px bg-theme-accent" />
                )}
              </button>
            ))}
          </div>

          <label className="relative block min-w-0 sm:w-56">
            <span className="sr-only">Search services</span>
            <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-theme-text-muted/60" />
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                setExpanded(false)
                setOpenMenuId(null)
              }}
              placeholder="Search services..."
              className="h-8 w-full rounded-lg border border-theme-border bg-theme-bg/45 pl-8 pr-3 text-xs text-theme-text outline-none transition-colors placeholder:text-theme-text-muted/60 focus:border-theme-accent/45"
            />
          </label>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-theme-border" style={TECH_TILE_STYLE}>
        <div className="hidden grid-cols-[minmax(220px,1.5fr)_108px_96px_132px_112px_32px] gap-4 border-b border-theme-border px-4 py-2.5 text-[9px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/75 md:grid">
          <span>Service</span>
          <span>Status</span>
          <span>Uptime</span>
          <span>CPU</span>
          <span>RAM</span>
          <span />
        </div>

        <div className="divide-y divide-theme-border">
          {visibleServices.length > 0 ? (
            visibleServices.map(service => (
              <ServiceTableRow
                key={service.id}
                service={service}
                cpuSamples={cpuHistory[service.id] || []}
                actionState={actionState[service.id]}
                menuOpen={openMenuId === service.id}
                onToggleMenu={() => setOpenMenuId(current => current === service.id ? null : service.id)}
                onRestart={async () => {
                  setOpenMenuId(null)
                  setActionState(current => ({ ...current, [service.id]: { type: 'loading', text: 'Restarting...' } }))
                  try {
                    const res = await fetch(`/api/services/${encodeURIComponent(service.id)}/restart`, { method: 'POST' })
                    const data = await res.json().catch(() => ({}))
                    if (!res.ok) throw new Error(data.detail || data.error || 'Restart failed')
                    setActionState(current => ({ ...current, [service.id]: { type: 'success', text: 'Restarted' } }))
                    window.setTimeout(() => {
                      setActionState(current => {
                        const next = { ...current }
                        delete next[service.id]
                        return next
                      })
                    }, 3500)
                  } catch (error) {
                    setActionState(current => ({
                      ...current,
                      [service.id]: { type: 'error', text: error?.message || 'Restart failed' },
                    }))
                  }
                }}
              />
            ))
          ) : (
            <div className="px-4 py-8 text-center text-xs text-theme-text-muted">
              No services match this view.
            </div>
          )}
        </div>

        {hiddenCount > 0 && (
          <button
            type="button"
            onClick={() => {
              setExpanded(true)
              setOpenMenuId(null)
            }}
            className="flex w-full items-center gap-2 border-t border-theme-border px-4 py-2 text-left text-[11px] font-medium text-theme-text-muted transition-colors hover:text-theme-text-secondary"
          >
            +{hiddenCount} more services
            <ChevronDown size={13} />
          </button>
        )}

        {expanded && filteredServices.length > 4 && (
          <button
            type="button"
            onClick={() => {
              setExpanded(false)
              setOpenMenuId(null)
            }}
            className="flex w-full items-center gap-2 border-t border-theme-border px-4 py-2 text-left text-[11px] font-medium text-theme-text-muted transition-colors hover:text-theme-text-secondary"
          >
            Show fewer services
          </button>
        )}
      </div>
    </section>
  )
})

const ServiceTableRow = memo(function ServiceTableRow({
  service,
  cpuSamples,
  actionState,
  menuOpen,
  onToggleMenu,
  onRestart,
}) {
  const isRestarting = actionState?.type === 'loading'
  const status = getStatusTone(isRestarting ? 'restarting' : service.status)
  const isRestartable = service.restartable === true

  return (
    <div
      className="grid gap-3 px-4 py-3 transition-colors hover:bg-theme-surface-hover/70 md:grid-cols-[minmax(220px,1.5fr)_108px_96px_132px_112px_32px] md:items-center md:gap-4"
      data-testid={`service-row-${service.id}`}
    >
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.dot}`} />
          <span className="truncate text-xs font-semibold text-theme-text">{service.name}</span>
          {service.hasSemantics && !service.required && (
            <span className="shrink-0 rounded border border-theme-border bg-theme-bg/35 px-1.5 py-0.5 text-[8px] font-semibold uppercase text-theme-text-muted/70">
              Optional
            </span>
          )}
          <LlmSwapPill llm={service.llm} />
        </div>
        <div className="mt-1 truncate pl-3.5 text-[10px] text-theme-text-muted/65">
          {service.description}
          {service.port ? <span className="md:hidden"> · :{service.port}</span> : null}
        </div>
      </div>

      <div>
        <span className={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold ${status.pill} ${status.text}`}>
          {status.label}
        </span>
      </div>

      <div className="font-mono text-xs text-theme-text-muted md:text-theme-text-secondary">
        {formatUptime(service.uptime)}
      </div>

      <div className="flex items-center gap-3">
        <span className="w-10 font-mono text-xs text-theme-text-secondary">
          {formatCpuPercent(service.cpuPercent)}
        </span>
        <ServiceSparkline samples={cpuSamples} />
      </div>

      <div className="font-mono text-xs text-theme-text-secondary">
        {formatRamMb(service.memoryUsedMb)}
      </div>

      <div className="relative flex items-center md:justify-end">
        {isRestartable && actionState && actionState.type !== 'loading' && (
          <span
            className={`mr-2 hidden max-w-24 truncate text-[10px] font-medium md:inline ${
              actionState.type === 'error' ? 'text-red-300' : 'text-emerald-300'
            }`}
            title={actionState.text}
          >
            {actionState.text}
          </span>
        )}
        {isRestartable ? (
          <button
            type="button"
            onClick={onToggleMenu}
            className="flex h-7 w-7 items-center justify-center rounded-md text-theme-text-muted transition-colors hover:bg-theme-surface-hover hover:text-theme-text-secondary"
            aria-label={`${service.name} actions`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <MoreHorizontal size={14} />
          </button>
        ) : (
          <span
            className="hidden h-7 w-7 md:block"
            title={service.restartUnavailableReason || 'Restart unavailable'}
            aria-hidden="true"
          />
        )}
        {isRestartable && menuOpen && (
          <div
            role="menu"
            className="absolute right-0 top-8 z-20 w-44 overflow-hidden rounded-lg border border-theme-border bg-theme-card py-1 shadow-2xl shadow-black/30"
            style={TECH_TILE_STYLE}
          >
            <button
              type="button"
              role="menuitem"
              onClick={onRestart}
              disabled={isRestarting}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-[11px] font-semibold text-theme-text-secondary transition-colors hover:bg-theme-surface-hover hover:text-theme-text disabled:cursor-wait disabled:opacity-60"
            >
              <span>{isRestarting ? 'Restarting...' : 'Restart service'}</span>
              <Power size={12} />
            </button>
          </div>
        )}
      </div>
    </div>
  )
})

const ServiceSparkline = memo(function ServiceSparkline({ samples }) {
  const values = samples.map(sample => sample.cpu)
  const hasSeries = values.length >= 2
  const max = Math.max(...values, 1)
  const width = 74
  const height = 20
  const points = values.map((value, index) => ({
    x: (width / Math.max(values.length - 1, 1)) * index,
    y: height - clamp(value / max, 0, 1) * (height - 4) - 2,
  }))
  const path = hasSeries ? buildSignalPath(points) : ''

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-5 w-20 overflow-visible"
      aria-hidden="true"
    >
      {hasSeries ? (
        <path
          d={path}
          fill="none"
          stroke="rgba(168,85,247,0.95)"
          strokeWidth="2"
          strokeLinecap="round"
        />
      ) : (
        <line
          x1="0"
          x2={width}
          y1={height / 2}
          y2={height / 2}
          stroke="rgba(255,255,255,0.12)"
          strokeWidth="1"
          strokeDasharray="3 4"
        />
      )}
    </svg>
  )
})

// BootstrapBanner moved to App.jsx for app-wide visibility
