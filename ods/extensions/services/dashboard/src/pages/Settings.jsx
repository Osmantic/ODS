import {
  Activity,
  ArrowUpRight,
  Calendar,
  ChevronDown,
  ChevronRight,
  Clock3,
  Crown,
  CreditCard,
  Database,
  Download,
  Gauge,
  HardDrive,
  Network,
  Palette,
  RefreshCw,
  Route,
  Server,
  Settings as SettingsIcon,
  UserPlus,
  WalletCards,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import EnvEditor from '../components/settings/EnvEditor'
import { useTheme } from '../contexts/ThemeContext'
import {
  clearSettingsFollowUp,
  loadSettingsFollowUp,
  saveSettingsFollowUp,
  settleSettingsApplyPlan,
} from '../utils/settingsApplyPlan'

const fetchJson = async (url, ms = 8000, options = {}) => {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), ms)
  try {
    return await fetch(url, { ...options, headers: options.headers || undefined, signal: c.signal })
  } finally {
    clearTimeout(t)
  }
}

const buildErrorFromResponse = async (response) => {
  let detail = null
  try {
    const payload = await response.json()
    detail = payload?.detail ?? payload
  } catch {}
  const error = new Error(typeof detail === 'string' ? detail : (detail?.message || `Request failed (${response.status})`))
  error.details = typeof detail === 'object' && detail ? detail : null
  return error
}

const fetchPayload = async (url, ms = 8000, options = {}) => {
  const response = await fetchJson(url, ms, options)
  if (!response.ok) throw await buildErrorFromResponse(response)
  return response.json()
}

const formatUptime = (secs = 0) => {
  const hours = Math.floor(secs / 3600)
  const mins = Math.floor((secs % 3600) / 60)
  return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`
}

const formatInstallDate = (value) => {
  if (!value) return 'Unknown'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + '  -  '
    + parsed.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

const formatCheckedAt = (value) => {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toLocaleString()
}

const formatUsageSource = (source) => {
  const status = source?.status
  if (!status) return 'Usage source unavailable'
  if (status === 'ok') return 'Token Spy connected'
  if (status === 'partial') return 'Partial usage telemetry'
  return titleCase(status)
}

const getErrorText = (err) => (
  err?.name === 'AbortError' ? 'Request timed out' : (err?.details?.message || err?.message || 'Failed to load settings')
)

const getDashboardHost = () => (typeof window !== 'undefined' ? window.location.hostname : 'localhost')
const getExternalUrl = (port) => (port ? `http://${getDashboardHost()}:${port}` : null)

const todayKey = () => {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

const formatCompact = (value) => {
  const number = Number(value || 0)
  if (number >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(1)}B`
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(2).replace(/\.00$/, '')}M`
  if (number >= 1_000) return `${(number / 1_000).toFixed(1).replace(/\.0$/, '')}k`
  return `${Math.round(number)}`
}

const titleCase = (value) => String(value || '')
  .replace(/[_-]+/g, ' ')
  .replace(/\b\w/g, char => char.toUpperCase())

const matchesEnvSearch = (key, field, query) => {
  if (!query) return true
  return [key, field?.label, field?.description].filter(Boolean).join(' ').toLowerCase().includes(query)
}

const routeSeverityOrder = { down: 0, unhealthy: 1, degraded: 2, unknown: 3, healthy: 4 }
const sortRoutesBySeverity = (items) => [...(items || [])].sort((a, b) => (routeSeverityOrder[a.status] ?? 9) - (routeSeverityOrder[b.status] ?? 9))
const routeFilterDotClass = {
  online: 'bg-emerald-400',
  degraded: 'bg-amber-400',
  inactive: 'bg-red-400',
}

const THEME_SWATCHES = {
  ods: 'linear-gradient(135deg, #9d00ff 0%, #18181b 100%)',
  lemonade: 'linear-gradient(135deg, #facc15 0%, #fdfbf3 100%)',
  light: 'linear-gradient(135deg, #60a5fa 0%, #ffffff 100%)',
  arctic: 'linear-gradient(135deg, #38bdf8 0%, #f0f9ff 100%)',
}

const ROUTE_DESCRIPTIONS = {
  ape: 'Policy evaluation and enforcement',
  comfyui: 'Text-to-image and image generation',
  dashboard: 'Main dashboard and control center',
  'dashboard-api': 'System status and metrics API',
  hermes: 'Advanced agent console',
  'hermes-proxy': 'Auth-gated Hermes LAN entry',
  litellm: 'OpenAI-compatible model gateway',
  'llama-server': 'Local inference backend',
  'open-webui': 'Primary chat interface',
  perplexica: 'Deep research and web synthesis',
  searxng: 'Private metasearch backend',
  'token-spy': 'Usage and token telemetry',
  whisper: 'Speech-to-text service',
  tts: 'Text-to-speech service',
}

const getServiceDescription = (service) => {
  if (service?.description) return service.description
  const id = String(service?.id || '').toLowerCase()
  const name = String(service?.name || '').toLowerCase()
  if (ROUTE_DESCRIPTIONS[id]) return ROUTE_DESCRIPTIONS[id]
  if (ROUTE_DESCRIPTIONS[name]) return ROUTE_DESCRIPTIONS[name]
  if (service?.category) return `${titleCase(service.category)} service`
  return 'Service registered in the current ODS stack'
}

export default function Settings() {
  const { theme, setTheme, themes, labels } = useTheme()
  const [version, setVersion] = useState(null)
  const [storage, setStorage] = useState(null)
  const [services, setServices] = useState([])
  const [usageReport, setUsageReport] = useState(null)
  const [envEditor, setEnvEditor] = useState(null)
  const [envValues, setEnvValues] = useState({})
  const [envValuesOriginal, setEnvValuesOriginal] = useState({})
  const [envSearch, setEnvSearch] = useState('')
  const [envActiveSection, setEnvActiveSection] = useState(null)
  const [envSaving, setEnvSaving] = useState(false)
  const [envApplying, setEnvApplying] = useState(false)
  const [envIssues, setEnvIssues] = useState([])
  const [envRevealSecrets, setEnvRevealSecrets] = useState({})
  const [envClearedSecrets, setEnvClearedSecrets] = useState([])
  const [envApplyPlan, setEnvApplyPlan] = useState(null)
  const [envFollowUpPlan, setEnvFollowUpPlan] = useState(() => loadSettingsFollowUp())
  const [statusCache, setStatusCache] = useState(null)
  const [setupStatus, setSetupStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [routeFilter, setRouteFilter] = useState('all')
  const [routesExpanded, setRoutesExpanded] = useState(false)
  const [envOpen, setEnvOpen] = useState(true)
  const envEditorRef = useRef(null)

  useEffect(() => { fetchSettings() }, [])

  const applyEnvEditorPayload = (payload) => {
    setEnvEditor(payload)
    setEnvValues(payload?.values || {})
    setEnvValuesOriginal(payload?.values || {})
    setEnvIssues(payload?.issues || [])
    setEnvRevealSecrets({})
    setEnvClearedSecrets([])
    setEnvApplyPlan(payload?.applyPlan || null)
    setEnvActiveSection(current => (current && payload?.sections?.some(section => section.id === current)) ? current : (payload?.sections?.[0]?.id || null))
  }

  const fetchVersionInfo = async ({ announce = false } = {}) => {
    try {
      const versionData = await fetchPayload('/api/version', 4000)
      setVersion(prev => ({
        ...(prev || {}),
        current: versionData.current,
        version: versionData.current && versionData.current !== '0.0.0' ? versionData.current : (prev?.version || 'Unknown'),
        latest: versionData.latest || null,
        update_available: Boolean(versionData.update_available && versionData.latest && versionData.current && versionData.current !== '0.0.0' && versionData.latest !== versionData.current),
        changelog_url: versionData.changelog_url || null,
        checked_at: versionData.checked_at || new Date().toISOString(),
        update_check_ok: true,
      }))
      if (announce) {
        setNotice({
          type: versionData.update_available ? 'warn' : 'info',
          text: versionData.update_available && versionData.latest ? `Update available: v${versionData.latest}` : 'You are already on the latest available release.',
        })
      }
    } catch (err) {
      if (announce) setNotice({ type: 'warn', text: `Could not check updates right now: ${getErrorText(err)}` })
    }
  }

  const fetchEnvEditor = async ({ announce = false } = {}) => {
    const payload = await fetchPayload('/api/settings/env', 10000)
    applyEnvEditorPayload(payload)
    if (announce) setNotice({ type: 'info', text: 'Environment editor reloaded from disk.' })
  }

  const fetchSettings = async ({ preserveEnvChanges = false } = {}) => {
    const failures = []
    try {
      setLoading(true)
      setError(null)
      setNotice(null)
      const today = todayKey()
      const [summaryResult, storageResult, envResult, usageResult, setupResult] = await Promise.allSettled([
        fetchPayload('/api/settings/summary', 10000),
        fetchPayload('/api/storage', 12000),
        fetchPayload('/api/settings/env', 10000),
        fetchPayload(`/api/usage/report?start=${today}&end=${today}`, 10000),
        fetchPayload('/api/setup/status', 8000),
      ])

      if (summaryResult.status === 'fulfilled') {
        const statusData = summaryResult.value
        setStatusCache(statusData)
        setVersion({
          version: statusData.version || 'Unknown',
          install_date: formatInstallDate(statusData.install_date),
          tier: titleCase(statusData.tier || 'Community'),
          uptime: formatUptime(statusData.uptime || 0),
        })
        setServices(statusData.services || [])
      } else failures.push(summaryResult.reason)

      if (storageResult.status === 'fulfilled') setStorage(storageResult.value)
      else failures.push(storageResult.reason)

      if (envResult.status === 'fulfilled') {
        if (!preserveEnvChanges) applyEnvEditorPayload(envResult.value)
      }
      else failures.push(envResult.reason)

      if (usageResult.status === 'fulfilled') setUsageReport(usageResult.value)
      else setUsageReport(null)

      if (setupResult.status === 'fulfilled') setSetupStatus(setupResult.value)
      else setSetupStatus(null)

      if (failures.length === 3) setError(getErrorText(failures[0]))
      else if (failures.length > 0) setNotice({ type: 'warn', text: 'Some settings details are temporarily unavailable. Showing the data that loaded successfully.' })
      else if (preserveEnvChanges) setNotice({ type: 'info', text: 'System details refreshed. Unsaved environment changes were preserved.' })
    } catch (err) {
      setError(getErrorText(err))
      console.error('Settings fetch error:', err)
    } finally {
      setLoading(false)
    }
    void fetchVersionInfo()
  }

  const handleOpenEnvironmentEditor = () => {
    setEnvOpen(true)
    window.requestAnimationFrame(() => {
      envEditorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const handleSaveEnv = async () => {
    if (!envEditor) return
    setEnvSaving(true)
    try {
      const payload = await fetchPayload('/api/settings/env', 15000, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'form', values: envValues, clearSecrets: envClearedSecrets }),
      })
      applyEnvEditorPayload(payload)
      setNotice({ type: 'info', text: `.env saved.${payload?.backupPath ? ` Backup: ${payload.backupPath}.` : ''} ${payload?.applyPlan?.summary || 'Restart or rebuild the stack to apply service-level changes.'}` })
    } catch (err) {
      if (err?.details?.issues?.length) setEnvIssues(err.details.issues)
      setNotice({ type: 'danger', text: getErrorText(err) })
    } finally {
      setEnvSaving(false)
    }
  }

  const handleApplyEnv = async () => {
    if (!envApplyPlan?.supported || !envApplyPlan?.services?.length) return
    setEnvApplying(true)
    try {
      const payload = await fetchPayload('/api/settings/env/apply', 180000, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_ids: envApplyPlan.services }),
      })
      const { remainingPlan, followUpPlan } = settleSettingsApplyPlan(envApplyPlan)
      setEnvApplyPlan(remainingPlan)
      if (followUpPlan) {
        setEnvFollowUpPlan(saveSettingsFollowUp(followUpPlan))
      }
      setNotice({
        type: 'info',
        text: followUpPlan
          ? `${payload?.message || 'Runtime changes applied successfully.'} Complete the required follow-up below.`
          : (payload?.message || 'Runtime changes applied successfully.'),
      })
    } catch (err) {
      setNotice({ type: 'danger', text: getErrorText(err) })
    } finally {
      setEnvApplying(false)
    }
  }

  const handleCompleteEnvFollowUp = () => {
    clearSettingsFollowUp()
    setEnvFollowUpPlan(null)
    setNotice({ type: 'info', text: 'Required follow-up marked complete.' })
  }

  const handleExportConfig = async () => {
    try {
      const data = statusCache || (await (await fetchJson('/api/status')).json())
      const config = {
        exported_at: new Date().toISOString(),
        version: data.version,
        tier: data.tier,
        gpu: data.gpu,
        services: data.services?.map(s => ({ name: s.name, port: s.port, status: s.status })),
        model: data.model,
      }
      const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ods-config-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      setNotice({ type: 'info', text: 'Configuration exported.' })
    } catch (err) {
      setNotice({ type: 'danger', text: `Export failed: ${err.message}` })
    }
  }

  const envFields = envEditor?.fields || {}
  const envSections = (envEditor?.sections || [])
    .map(section => ({ ...section, keys: section.keys.filter(key => matchesEnvSearch(key, envFields[key], envSearch.trim().toLowerCase())) }))
    .filter(section => section.keys.length > 0)
  const activeEnvSection = envSections.find(section => section.id === envActiveSection) || envSections[0] || null
  const envDirty = envClearedSecrets.length > 0 || JSON.stringify(envValues) !== JSON.stringify(envValuesOriginal)
  const envIssueMap = envIssues.reduce((acc, issue) => {
    if (issue?.key) (acc[issue.key] ||= []).push(issue.message)
    return acc
  }, {})

  const routeCounts = useMemo(() => {
    const online = services.filter(service => service.status === 'healthy')
    const degraded = services.filter(service => service.status === 'degraded')
    const inactive = services.filter(service => ['down', 'unhealthy', 'unknown'].includes(service.status))
    return { online, degraded, inactive }
  }, [services])

  if (loading) return <SettingsSkeleton />

  return (
    <div className="min-h-full px-3 py-6 sm:px-4 lg:px-5 xl:px-6">
      <SettingsPageHeader
        onRefresh={() => fetchSettings({ preserveEnvChanges: envDirty })}
        onCheckUpdates={() => {
          setNotice({ type: 'info', text: 'Checking for updates...' })
          void fetchVersionInfo({ announce: true })
        }}
        onOpenEnvironment={handleOpenEnvironmentEditor}
      />

      {error ? <Banner tone="danger">{error} - <button className="underline" onClick={fetchSettings}>Retry</button></Banner> : null}
      {notice ? <Banner tone={notice.type} onClose={() => setNotice(null)}>{notice.text}</Banner> : null}

      <div className="w-full space-y-5">
        <div className="grid items-stretch gap-4 xl:grid-cols-12">
          <SystemIdentityCard version={version} className="xl:col-span-7" />
          <AppearanceCard theme={theme} themes={themes} labels={labels} onThemeChange={setTheme} className="xl:col-span-5" />
          <AccountUsageCard usageReport={usageReport} className="xl:col-span-7" />
          <RemoteSetupCard setupStatus={setupStatus} className="xl:col-span-5" />
        </div>
        <RoutingTableCard
          services={services}
          counts={routeCounts}
          routeFilter={routeFilter}
          onRouteFilterChange={setRouteFilter}
          expanded={routesExpanded}
          onToggleExpanded={() => setRoutesExpanded(current => !current)}
        />

        <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(22rem,0.85fr)]">
          <StorageCard storage={storage} />
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            <UpdatesCard version={version} onCheckUpdates={() => fetchVersionInfo({ announce: true })} />
            <CommandsCard onExportConfig={handleExportConfig} />
          </div>
        </div>

        {envOpen && envEditor ? (
          <div ref={envEditorRef} className="pt-4">
            <EnvEditor
              editor={envEditor}
              search={envSearch}
              onSearchChange={setEnvSearch}
              sections={envSections}
              activeSection={activeEnvSection}
              onSectionChange={setEnvActiveSection}
              fields={envFields}
              values={envValues}
              issues={envIssues}
              issueMap={envIssueMap}
              revealedSecrets={envRevealSecrets}
              clearedSecrets={envClearedSecrets}
              onToggleReveal={(key) => setEnvRevealSecrets(current => ({ ...current, [key]: !current[key] }))}
              onClearSecret={(key) => setEnvClearedSecrets(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key])}
              onFieldChange={(key, value) => {
                setEnvClearedSecrets(current => current.filter(item => item !== key))
                setEnvValues(current => ({ ...current, [key]: value }))
              }}
              onRefresh={fetchSettings}
              onReload={() => fetchEnvEditor({ announce: true })}
              onSave={handleSaveEnv}
              onApply={handleApplyEnv}
              dirty={envDirty}
              saving={envSaving}
              applyPlan={envApplyPlan}
              followUpPlan={envFollowUpPlan}
              onCompleteFollowUp={handleCompleteEnvFollowUp}
              applying={envApplying}
            />
          </div>
        ) : null}
      </div>
    </div>
  )
}

function SettingsPageHeader({ onRefresh, onCheckUpdates, onOpenEnvironment }) {
  return (
    <header className="mb-7 flex w-full flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <h1 className="text-3xl font-semibold tracking-[-0.01em] text-theme-text sm:text-4xl">Settings</h1>
        <p className="mt-2 text-base text-theme-text-muted">Configure your ODS installation.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 lg:justify-end">
        <button onClick={onRefresh} className="flex h-11 items-center gap-2 rounded-lg px-3 text-sm font-medium text-theme-accent-light transition-colors hover:bg-theme-surface-hover hover:text-theme-text">
          <RefreshCw size={16} />
          Refresh
        </button>
        <button onClick={onCheckUpdates} className="flex h-11 items-center rounded-lg border border-theme-border bg-theme-card px-4 text-sm font-medium text-theme-text transition-colors hover:border-theme-accent/50">
          <span className="flex items-center gap-2"><RefreshCw size={15} />Check for Updates</span>
        </button>
        <button onClick={onOpenEnvironment} className="liquid-metal-button flex h-11 items-center rounded-lg px-5 text-sm font-semibold text-white">
          <span className="flex items-center gap-2">Open Environment Editor<ChevronRight size={16} /></span>
        </button>
      </div>
    </header>
  )
}

function SystemIdentityCard({ version, className = '' }) {
  const currentVersion = version?.version && version.version !== 'Unknown' ? `v${String(version.version).replace(/^v/i, '')}` : 'Unknown'
  const versionBadge = version?.update_check_ok
    ? (version?.update_available ? 'Update' : 'Latest')
    : null
  return (
    <PremiumCard className={`p-5 lg:p-6 ${className}`}>
      <CardIntro icon={Server} title="System Identity" description="Core information about this ODS instance." />
      <div className="mt-6 grid gap-y-5 border-t border-theme-border pt-5 sm:grid-cols-2 sm:gap-x-0 2xl:grid-cols-4">
        <MetaTile icon={Server} label="Version" value={currentVersion} badge={versionBadge} />
        <MetaTile icon={Calendar} label="Install Date" value={version?.install_date || 'Unknown'} />
        <MetaTile icon={Crown} label="Tier" value={version?.tier || 'Community'} />
        <MetaTile icon={Clock3} label="Uptime" value={version?.uptime || 'Unknown'} live />
      </div>
    </PremiumCard>
  )
}

function AppearanceCard({ theme, themes, labels, onThemeChange, className = '' }) {
  return (
    <PremiumCard className={`p-5 lg:p-6 ${className}`}>
      <CardIntro icon={Palette} title="Appearance" description="Choose how the dashboard appears on this browser." />
      <div className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-theme-border bg-theme-border">
        {themes.map(themeId => (
          <button
            key={themeId}
            type="button"
            aria-pressed={theme === themeId}
            onClick={() => onThemeChange(themeId)}
            className={`flex min-h-14 items-center justify-between gap-3 bg-theme-bg/40 px-4 text-sm font-semibold transition-colors ${
              theme === themeId
                ? 'text-theme-accent-light shadow-[inset_3px_0_0_rgb(var(--theme-accent))]'
                : 'text-theme-text-muted hover:bg-theme-surface-hover hover:text-theme-text'
            }`}
          >
            <span className="flex items-center gap-2.5">
              <span
                className="h-3 w-3 shrink-0 rounded-full border border-theme-border"
                style={{ background: THEME_SWATCHES[themeId] || 'rgb(var(--theme-accent))' }}
              />
              <span>{labels[themeId] || themeId}</span>
            </span>
            <span className={`h-1.5 w-1.5 rounded-full ${theme === themeId ? 'bg-theme-accent-light' : 'bg-transparent'}`} />
          </button>
        ))}
      </div>
    </PremiumCard>
  )
}

function AccountUsageCard({ usageReport, className = '' }) {
  const summary = usageReport?.summary || {}
  const modelsUsed = Array.isArray(usageReport?.models)
    ? new Set(usageReport.models.map(row => row?.model).filter(Boolean)).size
    : 0
  const hasActivity = Number(summary.requests || 0) > 0 || Number(summary.total_tokens || 0) > 0
  const usageSource = formatUsageSource(usageReport?.source)
  const lastActivity = hasActivity ? 'Today' : 'No activity today'
  return (
    <PremiumCard as={Link} to="/usage" className={`group p-5 lg:p-6 ${className}`}>
      <CardIntro icon={CreditCard} title="Account" description="Usage, tokens, requests, and activity at a glance." />
      <div className="mt-6 grid gap-5 border-y border-theme-border py-5 sm:grid-cols-2 2xl:grid-cols-4">
        <MetricTile icon={Gauge} label="Tokens (24h)" value={formatCompact(summary.total_tokens)} delta={hasActivity ? 'Live' : null} />
        <MetricTile icon={Database} label="Requests (24h)" value={formatCompact(summary.requests)} delta={hasActivity ? 'Tracked' : null} />
        <MetricTile icon={WalletCards} label="Models Used" value={formatCompact(modelsUsed)} delta={modelsUsed ? 'Tracked' : null} />
        <MetricTile icon={Clock3} label="Last Activity" value={lastActivity} delta={hasActivity ? 'Tracked' : null} />
      </div>
      <div className="mt-4 flex items-center justify-between gap-4 text-sm">
        <span className="flex min-w-0 items-center gap-2 text-theme-text-muted">
          <span className={`h-2 w-2 shrink-0 rounded-full ${usageReport?.source?.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400'}`} />
          <span className="truncate">{usageSource}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2 font-medium text-theme-accent-light">
          <Activity size={15} />
          Open usage
          <ChevronRight size={16} className="transition-transform group-hover:translate-x-1" />
        </span>
      </div>
    </PremiumCard>
  )
}

function RemoteSetupCard({ setupStatus, className = '' }) {
  const setupComplete = setupStatus ? !setupStatus.first_run : null
  const setupLabel = setupComplete === null ? 'Setup status unavailable' : setupComplete ? 'Setup complete' : `Setup step ${setupStatus.step || 0}`
  const personaLabel = setupStatus?.persona ? `Active persona: ${titleCase(setupStatus.persona)}` : 'No persona selected'

  return (
    <PremiumCard as={Link} to="/invites" className={`group flex h-full flex-col p-5 lg:p-6 ${className}`}>
      <CardIntro icon={UserPlus} title="Remote Setup" description="Manage owner and collaborator access for this installation." />
      <div className="mt-6 flex flex-1 flex-col justify-between border-t border-theme-border pt-5">
        <div>
          <p className="flex items-center gap-2 text-sm font-semibold text-theme-text">
            <span className={`h-2 w-2 rounded-full ${setupComplete ? 'bg-emerald-400' : 'bg-amber-400'}`} />
            {setupLabel}
          </p>
          <p className="mt-3 text-sm leading-6 text-theme-text-muted">{personaLabel}. Owner and collaborator access is managed with invite links.</p>
        </div>
        <span className="mt-5 flex items-center justify-between border-t border-theme-border pt-4 text-sm font-medium text-theme-accent-light">
          <span className="flex items-center gap-2"><UserPlus size={15} />Manage owner access</span>
          <ChevronRight size={17} className="transition-transform group-hover:translate-x-1" />
        </span>
      </div>
    </PremiumCard>
  )
}

function RoutingTableCard({ services, counts, routeFilter, onRouteFilterChange, expanded, onToggleExpanded }) {
  const allRoutes = sortRoutesBySeverity(services)
  const filteredRoutes = routeFilter === 'online'
    ? counts.online
    : routeFilter === 'degraded'
      ? counts.degraded
      : routeFilter === 'inactive'
        ? counts.inactive
        : allRoutes
  const visibleRoutes = expanded ? filteredRoutes : filteredRoutes.slice(0, 4)
  const hiddenCount = Math.max(filteredRoutes.length - visibleRoutes.length, 0)

  return (
    <PremiumCard className="grid overflow-hidden lg:grid-cols-[340px_1fr]">
      <div className="border-b border-theme-border p-5 lg:border-b-0 lg:border-r lg:p-6">
        <CardIntro icon={Route} title="Routing Table" description="Overview of route surfaces and their current status." />
        <div className="mt-9">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted">Route Surfaces</p>
          <div className="mt-3 inline-flex items-center gap-3 rounded-full border border-theme-border bg-theme-accent/10 px-4 py-2 text-sm text-theme-text">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            {getDashboardHost()}
          </div>
        </div>
      </div>
      <div className="p-5 lg:p-6">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-wrap gap-2">
            {['all', 'online', 'degraded', 'inactive'].map(item => (
              <button
                key={item}
                type="button"
                onClick={() => onRouteFilterChange(item)}
                className={`rounded-full border px-4 py-2 text-sm capitalize transition-colors ${
                  routeFilter === item
                    ? 'border-theme-accent bg-theme-accent text-white shadow-[0_0_26px_rgba(157,0,255,0.28)]'
                    : 'border-theme-border bg-theme-card text-theme-text-muted hover:bg-theme-surface-hover hover:text-theme-text'
                }`}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-4">
            <p className="text-sm text-theme-text-muted">{services.length} routes total</p>
            <Link to="/extensions/integrations" className="inline-flex items-center gap-2 rounded-lg border border-theme-border bg-theme-card px-4 py-2 text-sm text-theme-text hover:border-theme-accent/50">
              View All Routes
              <ChevronRight size={15} />
            </Link>
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <RouteStatusCard tone="online" label="Online" count={counts.online.length} description="Healthy services in the current status cache" />
          <RouteStatusCard tone="degraded" label="Degraded" count={counts.degraded.length} description="Services reporting degraded health" />
          <RouteStatusCard tone="inactive" label="Inactive" count={counts.inactive.length} description="Down, unhealthy, or unknown services" />
        </div>

        <div className="mt-5">
          <p className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted">
            <span className={`h-2 w-2 rounded-full ${routeFilter === 'all' ? 'bg-theme-accent' : routeFilterDotClass[routeFilter]}`} />
            {routeFilter === 'all' ? 'All Routes' : `${titleCase(routeFilter)} Routes`}
          </p>
          <div className="overflow-hidden rounded-lg border border-theme-border bg-theme-bg/30">
            {visibleRoutes.length > 0 ? visibleRoutes.map(service => (
              <RouteRow key={`${service.id || service.name}-${service.port || 'internal'}`} service={service} />
            )) : (
              <div className="px-5 py-6 text-sm text-theme-text-muted">No routes match this filter.</div>
            )}
            {hiddenCount > 0 ? (
              <button type="button" onClick={onToggleExpanded} className="flex w-full items-center gap-3 border-t border-theme-border px-5 py-3 text-left text-sm text-theme-text-muted hover:bg-theme-surface-hover hover:text-theme-text">
                <span className="text-lg leading-none">+</span>
                {hiddenCount} more routes
                <ChevronDown size={15} className={expanded ? 'rotate-180' : ''} />
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </PremiumCard>
  )
}

function StorageCard({ storage }) {
  const totalDataGb = Math.max(Number(storage?.total_data?.gb) || 0, 0)
  const modelsGb = Math.max(Number(storage?.models?.gb) || 0, 0)
  const vectorGb = Math.max(Number(storage?.vector_db?.gb) || 0, 0)
  const serviceDataGb = Math.max(totalDataGb - modelsGb - vectorGb, 0)
  const diskUsedGb = Math.max(Number(storage?.disk?.used_gb) || 0, 0)
  const diskTotalGb = Math.max(Number(storage?.disk?.total_gb) || 0, 0)
  const diskPercent = clampPercent(storage?.disk?.percent)
  const items = [
    ['Models', modelsGb],
    ['Vector DB', vectorGb],
    ['Service data', serviceDataGb],
  ]

  return (
    <UtilityCard icon={HardDrive} title="Storage" description="Persistent ODS data and host disk capacity.">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-theme-border bg-theme-bg/30 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-theme-text-muted">ODS data</p>
              <p className="mt-1 text-2xl font-semibold text-theme-text">
                {totalDataGb > 0 ? (storage?.total_data?.formatted || formatStorageGb(totalDataGb)) : 'No data yet'}
              </p>
            </div>
            <Database size={20} className="text-theme-text-muted" />
          </div>
          <div className="mt-4 flex items-center justify-between gap-3 border-t border-theme-border pt-3 text-xs">
            <span className="text-theme-text-muted">Data directory</span>
            <code className="rounded-md bg-theme-bg/55 px-2 py-1 text-theme-text">./data</code>
          </div>
        </div>

        <div className="rounded-lg border border-theme-border bg-theme-bg/30 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-theme-text-muted">Host disk</p>
              <p className="mt-1 text-2xl font-semibold text-theme-text">
                {diskTotalGb > 0 ? `${diskPercent}% used` : 'Unavailable'}
              </p>
            </div>
            <HardDrive size={20} className="text-theme-text-muted" />
          </div>
          <p className="mt-2 text-xs text-theme-text-muted">
            {diskTotalGb > 0 ? `${formatStorageGb(diskUsedGb)} of ${formatStorageGb(diskTotalGb)}` : 'Disk capacity unavailable'}
          </p>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-theme-border/70" aria-label={`Host disk ${diskPercent}% used`}>
            <div className="h-full rounded-full bg-theme-accent" style={{ width: `${diskPercent}%` }} />
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {items.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-theme-border bg-theme-bg/20 px-4 py-3">
            <p className="text-xs text-theme-text-muted">{label}</p>
            <p className="mt-1 text-base font-semibold text-theme-text">{value > 0 ? formatStorageGb(value) : 'Empty'}</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-theme-border/65">
              <div
                className="h-full rounded-full bg-theme-accent/75"
                style={{ width: `${totalDataGb > 0 ? clampPercent((value / totalDataGb) * 100) : 0}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      <p className="mt-3 text-xs text-theme-text-muted">
        Includes bind-mounted ODS service data. Docker image layers are managed separately by Docker.
      </p>
    </UtilityCard>
  )
}

function UpdatesCard({ version, onCheckUpdates }) {
  const checkedAt = formatCheckedAt(version?.checked_at)
  const updateText = version?.update_check_ok
    ? (version?.update_available ? 'Update available' : 'Current release')
    : 'Not checked yet'

  return (
    <PremiumCard className="flex min-h-0 flex-col justify-between p-4">
      <div className="flex items-start gap-3">
        <RefreshCw size={19} strokeWidth={1.8} className="mt-0.5 shrink-0 text-theme-accent-light" />
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-theme-text">Updates</h2>
          <p className="mt-0.5 truncate text-xs text-theme-text-muted">
            {checkedAt ? `Checked ${checkedAt}` : 'Release status has not been checked.'}
          </p>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between gap-4 border-t border-theme-border pt-3">
        <div>
          <p className="text-base font-semibold text-theme-text">
            {version?.update_available && version?.latest ? `v${version.latest}` : `v${version?.version || 'Unknown'}`}
          </p>
          <p className="text-xs text-theme-text-muted">{updateText}</p>
        </div>
        <button type="button" onClick={onCheckUpdates} className="inline-flex items-center gap-2 rounded-lg border border-theme-border bg-theme-card px-3 py-2 text-sm text-theme-text hover:border-theme-accent/50">
          <RefreshCw size={15} />
          Check
        </button>
      </div>
    </PremiumCard>
  )
}

function CommandsCard({ onExportConfig }) {
  return (
    <PremiumCard className="flex min-h-0 flex-col justify-between p-4">
      <div className="flex items-start gap-3">
        <SettingsIcon size={19} strokeWidth={1.8} className="mt-0.5 shrink-0 text-theme-accent-light" />
        <div>
          <h2 className="text-base font-semibold text-theme-text">Commands</h2>
          <p className="mt-0.5 text-xs text-theme-text-muted">Portable operational metadata.</p>
        </div>
      </div>
      <button type="button" onClick={onExportConfig} className="mt-4 flex w-full items-center justify-between border-t border-theme-border pt-3 text-left text-sm font-medium text-theme-text hover:text-theme-accent-light">
        <span className="flex items-center gap-2"><Download size={16} />Export configuration</span>
        <ArrowUpRight size={15} className="text-theme-text-muted" />
      </button>
    </PremiumCard>
  )
}

function CardIntro({ icon: Icon, title, description }) {
  return (
    <div className="flex min-w-0 items-start gap-3.5">
      <Icon size={21} strokeWidth={1.8} className="mt-0.5 shrink-0 text-theme-accent-light" />
      <div>
        <h2 className="text-lg font-semibold text-theme-text">{title}</h2>
        <p className="mt-1 max-w-md text-sm leading-5 text-theme-text-muted">{description}</p>
      </div>
    </div>
  )
}

function MetaTile({ icon: Icon, label, value, badge, live = false }) {
  return (
    <div className="min-w-0 px-4 first:pl-0 sm:border-r sm:border-theme-border sm:last:border-r-0 2xl:first:pl-0">
      <div className="mb-2 flex items-center gap-2 text-theme-text-muted">
        <Icon size={16} strokeWidth={1.8} />
        <span className="text-[11px] font-semibold uppercase tracking-[0.2em]">{label}</span>
      </div>
      <div className="flex min-w-0 items-center gap-3">
        <span className="truncate text-base font-semibold text-theme-text">{value}</span>
        {badge ? <span className="text-xs font-medium text-theme-accent-light">{badge}</span> : null}
        {live ? <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-300"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />Live</span> : null}
      </div>
    </div>
  )
}

function MetricTile({ icon: Icon, label, value, delta }) {
  return (
    <div className="min-w-0 border-theme-border 2xl:border-l 2xl:pl-5 2xl:first:border-l-0 2xl:first:pl-0">
      <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted"><Icon size={14} />{label}</p>
      <div className="mt-2 flex items-baseline gap-2">
        <p className="text-xl font-semibold text-theme-text">{value}</p>
        {delta ? <span className="text-xs font-medium text-theme-text-muted">{delta}</span> : null}
      </div>
    </div>
  )
}

function RouteStatusCard({ tone, label, count, description }) {
  const palette = {
    online: 'border-emerald-400/25 text-emerald-300 shadow-[inset_3px_0_0_rgba(52,211,153,0.55)]',
    degraded: 'border-amber-400/25 text-amber-300 shadow-[inset_3px_0_0_rgba(251,191,36,0.75)]',
    inactive: 'border-red-400/25 text-red-300 shadow-[inset_3px_0_0_rgba(248,113,113,0.65)]',
  }
  return (
    <div className={`rounded-lg border bg-theme-bg/30 p-5 ${palette[tone]}`}>
      <div className="mb-3 flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${tone === 'online' ? 'bg-emerald-400' : tone === 'degraded' ? 'bg-amber-400' : 'bg-red-400'}`} />
        <p className="text-sm font-semibold">{label}</p>
      </div>
      <p className="text-3xl font-semibold text-theme-text">{count}<span className="ml-2 text-sm font-normal text-theme-text-muted">routes</span></p>
      <p className="mt-2 text-sm text-theme-text-muted">{description}</p>
    </div>
  )
}

function RouteRow({ service }) {
  const href = getExternalUrl(service.port)
  const healthy = service.status === 'healthy'
  const degraded = service.status === 'degraded'
  const dot = healthy ? 'bg-emerald-400' : degraded ? 'bg-amber-400' : 'bg-red-400'
  const description = getServiceDescription(service)
  const content = (
    <>
      <div className="flex min-w-0 items-center gap-4">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-theme-text">{service.name}</p>
          <p className="truncate text-xs text-theme-text-muted">{description}</p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <span className="rounded-lg border border-theme-accent/35 bg-theme-accent/10 px-3 py-1 font-mono text-sm text-theme-accent-light">
          {service.port ? `:${service.port}` : 'internal'}
        </span>
        <ChevronRight size={18} className="text-theme-text-muted" />
      </div>
    </>
  )
  const className = "flex items-center justify-between gap-4 border-b border-theme-border px-5 py-3 last:border-b-0 hover:bg-theme-surface-hover"
  return href
    ? <a href={href} target="_blank" rel="noopener noreferrer" className={className}>{content}</a>
    : <div className={className}>{content}</div>
}

function UtilityCard({ icon: Icon, title, description, children }) {
  return (
    <PremiumCard className="p-5">
      <div className="mb-5">
        <CardIntro icon={Icon} title={title} description={description} />
      </div>
      {children}
    </PremiumCard>
  )
}

function PremiumCard({ as: Component = 'div', className = '', children, ...props }) {
  return (
    <Component
      className={`settings-premium-card liquid-metal-frame liquid-metal-frame--soft rounded-lg border ${className}`}
      {...props}
    >
      {children}
    </Component>
  )
}

function clampPercent(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  return Math.min(Math.max(number, 0), 100)
}

function formatStorageGb(value) {
  const number = Number(value)
  return `${Number.isFinite(number) ? Math.max(number, 0).toFixed(1) : '0.0'} GB`
}

function Banner({ tone = 'info', children, onClose }) {
  const cls = tone === 'danger' ? 'border-red-500/20 bg-red-500/10 text-red-200' : tone === 'warn' ? 'border-yellow-500/20 bg-yellow-500/10 text-yellow-100' : 'border-theme-accent/20 bg-theme-accent/10 text-theme-text'
  return (
    <div className={`mb-6 flex w-full items-center justify-between rounded-lg border p-4 text-sm ${cls}`}>
      <span>{children}</span>
      {onClose ? <button onClick={onClose} className="ml-4 opacity-60 hover:opacity-100">x</button> : null}
    </div>
  )
}

function SettingsSkeleton() {
  return (
    <div className="px-5 py-7 sm:px-8 xl:px-12">
      <div className="mx-auto max-w-[1760px] animate-pulse">
        <div className="mb-8 flex items-start justify-between">
          <div>
            <div className="mb-3 h-10 w-56 rounded-xl bg-theme-card" />
            <div className="h-4 w-80 rounded-lg bg-theme-card" />
          </div>
          <div className="h-11 w-72 rounded-xl bg-theme-card" />
        </div>
        <div className="space-y-5">
          {[...Array(4)].map((_, index) => <div key={index} className="h-32 rounded-lg bg-theme-card" />)}
        </div>
      </div>
    </div>
  )
}
