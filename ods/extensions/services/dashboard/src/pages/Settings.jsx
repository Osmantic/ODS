import {
  Settings as SettingsIcon,
  Server,
  HardDrive,
  RefreshCw,
  Download,
  Network,
  FileText,
  UserPlus,
  CreditCard,
  ChevronRight,
  Palette,
} from 'lucide-react'
import { useEffect, useState } from 'react'
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

const formatDateTime = (value) => {
  if (!value) return 'Unknown'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

const getErrorText = (err) => (
  err?.name === 'AbortError' ? 'Request timed out' : (err?.details?.message || err?.message || 'Failed to load settings')
)

const getDashboardHost = () => (typeof window !== 'undefined' ? window.location.hostname : 'localhost')
const getExternalUrl = (port) => (port ? `http://${getDashboardHost()}:${port}` : null)

const ROUTE_GROUP_STYLES = {
  inactive: { dot: 'bg-red-500', text: 'text-theme-text-secondary', line: 'rgba(239,68,68,0.26)' },
  degraded: { dot: 'bg-amber-400', text: 'text-theme-text-secondary', line: 'rgba(245,158,11,0.24)' },
  online: { dot: 'bg-emerald-400', text: 'text-theme-text-secondary', line: 'rgba(52,211,153,0.22)' },
}

const THEME_SWATCHES = {
  ods: 'linear-gradient(135deg, #9d00ff 0%, #18181b 100%)',
  lemonade: 'linear-gradient(135deg, #facc15 0%, #fdfbf3 100%)',
  light: 'linear-gradient(135deg, #60a5fa 0%, #ffffff 100%)',
  arctic: 'linear-gradient(135deg, #38bdf8 0%, #f0f9ff 100%)',
}

const routeSeverityOrder = { down: 0, unhealthy: 1, degraded: 2, unknown: 3, healthy: 4 }
const sortRoutesBySeverity = (items) => [...(items || [])].sort((a, b) => (routeSeverityOrder[a.status] ?? 9) - (routeSeverityOrder[b.status] ?? 9))

const matchesEnvSearch = (key, field, query) => {
  if (!query) return true
  return [key, field?.label, field?.description].filter(Boolean).join(' ').toLowerCase().includes(query)
}

export default function Settings() {
  const { theme, setTheme, themes, labels } = useTheme()
  const [version, setVersion] = useState(null)
  const [storage, setStorage] = useState(null)
  const [services, setServices] = useState([])
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

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
        checked_at: versionData.checked_at || null,
      }))
      if (announce) setNotice({ type: versionData.update_available ? 'warn' : 'info', text: versionData.update_available && versionData.latest ? `Update available: v${versionData.latest}` : 'You are already on the latest available release.' })
    } catch (err) {
      if (announce) setNotice({ type: 'warn', text: `Could not check updates right now: ${getErrorText(err)}` })
    }
  }

  const fetchEnvEditor = async ({ announce = false } = {}) => {
    const payload = await fetchPayload('/api/settings/env', 10000)
    applyEnvEditorPayload(payload)
    if (announce) setNotice({ type: 'info', text: 'Environment editor reloaded from disk.' })
  }

  const fetchSettings = async () => {
    const failures = []
    try {
      setLoading(true); setError(null); setNotice(null)
      const [summaryResult, storageResult, envResult] = await Promise.allSettled([
        fetchPayload('/api/settings/summary', 10000),
        fetchPayload('/api/storage', 12000),
        fetchPayload('/api/settings/env', 10000),
      ])

      if (summaryResult.status === 'fulfilled') {
        const statusData = summaryResult.value
        setStatusCache(statusData)
        setVersion({
          version: statusData.version || 'Unknown',
          install_date: formatDateTime(statusData.install_date),
          tier: statusData.tier,
          uptime: formatUptime(statusData.uptime || 0),
        })
        setServices(statusData.services || [])
      } else failures.push(summaryResult.reason)

      if (storageResult.status === 'fulfilled') setStorage(storageResult.value); else failures.push(storageResult.reason)
      if (envResult.status === 'fulfilled') applyEnvEditorPayload(envResult.value); else failures.push(envResult.reason)

      if (failures.length === 3) setError(getErrorText(failures[0]))
      else if (failures.length > 0) setNotice({ type: 'warn', text: 'Some settings details are temporarily unavailable. Showing the data that loaded successfully.' })
    } catch (err) {
      setError(getErrorText(err))
      console.error('Settings fetch error:', err)
    } finally {
      setLoading(false)
    }
    void fetchVersionInfo()
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
      const config = { exported_at: new Date().toISOString(), version: data.version, tier: data.tier, gpu: data.gpu, services: data.services?.map(s => ({ name: s.name, port: s.port, status: s.status })), model: data.model }
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

  const routingGroups = [
    { key: 'online', label: 'Online', tone: 'online', services: sortRoutesBySeverity(services).filter(service => service.status === 'healthy') },
    { key: 'degraded', label: 'Degraded', tone: 'degraded', services: sortRoutesBySeverity(services).filter(service => service.status === 'degraded') },
    { key: 'inactive', label: 'Inactive', tone: 'inactive', services: sortRoutesBySeverity(services).filter(service => ['down', 'unhealthy', 'unknown'].includes(service.status)) },
  ]

  const envFields = envEditor?.fields || {}
  const envSections = (envEditor?.sections || []).map(section => ({ ...section, keys: section.keys.filter(key => matchesEnvSearch(key, envFields[key], envSearch.trim().toLowerCase())) })).filter(section => section.keys.length > 0)
  const activeEnvSection = envSections.find(section => section.id === envActiveSection) || envSections[0] || null
  const envDirty = envClearedSecrets.length > 0 || JSON.stringify(envValues) !== JSON.stringify(envValuesOriginal)
  const envIssueMap = envIssues.reduce((acc, issue) => { if (issue?.key) (acc[issue.key] ||= []).push(issue.message); return acc }, {})

  if (loading) return (
    <div className="p-8 animate-pulse">
      <div className="mb-8 flex items-start justify-between"><div><div className="h-8 bg-theme-card rounded w-1/3 mb-3" /><div className="h-4 bg-theme-card rounded w-80" /></div><div className="h-10 bg-theme-card rounded-lg w-28" /></div>
      <div className="space-y-6 max-w-5xl">{[...Array(6)].map((_, i) => <div key={i} className="h-36 bg-theme-card rounded-xl" />)}</div>
    </div>
  )

  return (
    <div className="p-8">
      <div className="mb-8 flex items-center justify-between">
        <div><h1 className="text-2xl font-bold text-theme-text">Settings</h1><p className="text-theme-text-muted mt-1">Configure your ODS installation.</p></div>
        <button onClick={fetchSettings} className="text-sm text-theme-accent-light hover:text-theme-accent-light flex items-center gap-1.5 transition-colors"><RefreshCw size={14} />Refresh</button>
      </div>

      {error ? <Banner tone="danger">{error} — <button className="underline" onClick={fetchSettings}>Retry</button></Banner> : null}
      {notice ? <Banner tone={notice.type} onClose={() => setNotice(null)}>{notice.text}</Banner> : null}

      <div className="max-w-5xl space-y-6 liquid-metal-sequence-grid liquid-metal-sequence-grid--services">
        <SettingsSection title="System Identity" icon={Server}><div className="grid gap-4 sm:grid-cols-2"><InfoRow label="Version" value={version?.version || 'Unknown'} /><InfoRow label="Install Date" value={version?.install_date || 'Unknown'} /><InfoRow label="Tier" value={version?.tier || 'Community'} /><InfoRow label="Uptime" value={version?.uptime || 'Unknown'} /></div></SettingsSection>

        <SettingsSection title="Appearance" icon={Palette}>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-theme-text">Theme</p>
              <p className="mt-1 text-xs text-theme-text-muted">Choose how the dashboard appears on this browser.</p>
            </div>
            <div className="grid grid-cols-2 gap-1 rounded-lg border border-theme-border bg-black/[0.12] p-1 sm:inline-grid sm:grid-cols-4">
              {themes.map(themeId => (
                <ThemeOptionButton
                  key={themeId}
                  active={theme === themeId}
                  label={labels[themeId] || themeId}
                  swatch={THEME_SWATCHES[themeId]}
                  onClick={() => setTheme(themeId)}
                />
              ))}
            </div>
          </div>
        </SettingsSection>

        <SettingsSection title="Account" icon={SettingsIcon}>
          <div className="divide-y divide-theme-border">
            <SettingsNavRow
              to="/usage"
              icon={CreditCard}
              label="Usage"
              description="Token and request counts, model-level cost insights, and historical activity."
            />
          </div>
        </SettingsSection>

        <SettingsSection title="Remote Setup" icon={UserPlus}>
          <div className="divide-y divide-theme-border">
            <SettingsNavRow
              to="/invites"
              icon={UserPlus}
              label="Setup / Owner"
              description="Pre-stage a system for its intended owner, or create a temporary support invite."
            />
          </div>
        </SettingsSection>

        {services.length > 0 ? <SettingsSection title="Routing Table" icon={Network}><div className="space-y-3"><div className="flex flex-wrap items-center gap-2"><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/60">route surfaces</p><span className="rounded-full border border-white/10 bg-black/[0.12] px-2.5 py-1 text-[10px] font-mono uppercase tracking-[0.16em] text-theme-text">{getDashboardHost()}</span></div><div className="grid gap-3 xl:grid-cols-3">{routingGroups.map(group => <RoutingGroup key={group.key} {...group} />)}</div></div></SettingsSection> : null}

        {envEditor ? <SettingsSection title="Environment Editor" icon={FileText}><EnvEditor editor={envEditor} search={envSearch} onSearchChange={setEnvSearch} sections={envSections} activeSection={activeEnvSection} onSectionChange={setEnvActiveSection} fields={envFields} values={envValues} issues={envIssues} issueMap={envIssueMap} revealedSecrets={envRevealSecrets} clearedSecrets={envClearedSecrets} onToggleReveal={(key) => setEnvRevealSecrets(current => ({ ...current, [key]: !current[key] }))} onClearSecret={(key) => setEnvClearedSecrets(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key])} onFieldChange={(key, value) => { setEnvClearedSecrets(current => current.filter(item => item !== key)); setEnvValues(current => ({ ...current, [key]: value })) }} onReload={() => fetchEnvEditor({ announce: true })} onSave={handleSaveEnv} onApply={handleApplyEnv} dirty={envDirty} saving={envSaving} applyPlan={envApplyPlan} followUpPlan={envFollowUpPlan} onCompleteFollowUp={handleCompleteEnvFollowUp} applying={envApplying} /></SettingsSection> : null}

        <SettingsSection title="Storage" icon={HardDrive}><StorageBlock storage={storage} /></SettingsSection>
        <SettingsSection title="Updates" icon={RefreshCw}><div className="flex items-center justify-between gap-4"><div><p className="text-theme-text">{version?.update_available && version?.latest ? `Update available: v${version.latest}` : `Installed version: v${version?.version || 'Unknown'}`}</p><p className="text-sm text-theme-text-muted">{version?.checked_at ? `Last checked: ${new Date(version.checked_at).toLocaleString()}` : 'Checks GitHub in the background to avoid blocking the page.'}</p></div><button onClick={() => { setNotice({ type: 'info', text: 'Checking for updates...' }); void fetchVersionInfo({ announce: true }) }} className="liquid-metal-button px-4 py-2 text-white rounded-lg text-sm flex items-center gap-2"><RefreshCw size={16} />Check for Updates</button></div></SettingsSection>
        <SettingsSection title="Commands" icon={SettingsIcon}><ActionButton icon={Download} label="Export Configuration" description="Download your settings as a JSON file" onClick={handleExportConfig} /></SettingsSection>
      </div>
    </div>
  )
}

function SettingsSection({ title, icon: Icon, children }) { return <div className="liquid-metal-frame liquid-metal-sequence-card bg-theme-card border border-theme-border rounded-xl"><div className="flex items-center gap-3 p-4 border-b border-theme-border"><Icon size={20} className="text-theme-text-muted" /><h2 className="text-lg font-semibold text-theme-text">{title}</h2></div><div className="p-4">{children}</div></div> }
function InfoRow({ label, value }) { return <div className="flex items-center justify-between py-2 gap-4"><span className="text-sm text-theme-text-muted">{label}</span><span className="text-sm text-theme-text font-medium font-mono text-right break-all">{value}</span></div> }

function ThemeOptionButton({ active, label, swatch, onClick }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`flex h-9 items-center justify-center gap-2 rounded-md px-3 text-xs font-semibold transition-colors ${
        active ? 'bg-theme-accent/25 text-theme-accent-light' : 'text-theme-text-muted hover:text-theme-text'
      }`}
    >
      <span className="h-3 w-3 shrink-0 rounded-full border border-white/20" style={{ background: swatch || 'rgb(var(--theme-accent))' }} />
      <span>{label}</span>
    </button>
  )
}

function Banner({ tone = 'info', children, onClose }) {
  const cls = tone === 'danger' ? 'border-red-500/20 bg-red-500/10 text-red-200' : tone === 'warn' ? 'border-yellow-500/20 bg-yellow-500/10 text-yellow-100' : 'border-theme-accent/20 bg-theme-accent/10 text-theme-text'
  return <div className={`mb-6 rounded-xl border p-4 text-sm flex items-center justify-between ${cls}`}><span>{children}</span>{onClose ? <button onClick={onClose} className="ml-4 opacity-60 hover:opacity-100">×</button> : null}</div>
}

function RoutingGroup({ label, tone, services }) {
  const styles = ROUTE_GROUP_STYLES[tone]
  const [expanded, setExpanded] = useState(false)
  const primary = tone === 'online'
  const visible = primary ? (expanded ? services : services.slice(0, 4)) : (expanded ? services : [])
  const hiddenCount = Math.max(services.length - visible.length, 0)
  const summary = services.slice(0, 2).map(service => service.name).join(' · ')
  return <div className="self-start rounded-2xl border bg-theme-card px-3 py-2.5" style={{ borderColor: 'rgba(255,255,255,0.08)', boxShadow: `inset 2px 0 0 ${styles.line}` }}><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><span className={`h-1.5 w-1.5 rounded-full ${styles.dot}`} /><p className={`text-[10px] font-semibold uppercase tracking-[0.18em] ${styles.text}`}>{label}</p><span className="text-[10px] text-theme-text-muted/60">{services.length} {services.length === 1 ? 'route' : 'routes'}</span></div>{services.length > 0 ? <button type="button" onClick={() => setExpanded(current => !current)} className="text-[10px] font-mono uppercase tracking-[0.16em] text-theme-text-muted/65 transition-colors hover:text-theme-text">{expanded ? 'Collapse' : 'Show all'}</button> : null}</div>{visible.length > 0 ? <div className="mt-2 space-y-1">{visible.map(service => <RoutingRow key={`${tone}-${service.name}`} service={service} tone={tone} />)}{!expanded && hiddenCount > 0 ? <p className="px-1 pt-0.5 text-[10px] uppercase tracking-[0.14em] text-theme-text-muted/45">+{hiddenCount} more</p> : null}</div> : services.length === 0 ? <p className="mt-2 text-[10px] uppercase tracking-[0.14em] text-theme-text-muted/40">Clear</p> : <div className="mt-1.5 min-h-[1.75rem]"><p className="truncate text-[10px] uppercase tracking-[0.14em] text-theme-text-muted/52">{summary}{services.length > 2 ? ` +${services.length - 2} more` : ''}</p></div>}</div>
}

function RoutingRow({ service, tone }) {
  const styles = ROUTE_GROUP_STYLES[tone]
  const href = getExternalUrl(service.port)
  return <div className="flex items-center justify-between gap-3 rounded-lg border border-white/6 bg-black/[0.1] px-2 py-1.5"><div className="flex min-w-0 items-center gap-2"><span className={`h-1.5 w-1.5 shrink-0 rounded-full ${styles.dot}`} /><span className="truncate text-[11px] font-medium text-theme-text">{service.name}</span></div><div className="flex shrink-0 items-center gap-2 text-[9px] text-theme-text-muted/75">{href ? <a className="font-mono uppercase tracking-[0.14em] text-theme-accent-light hover:text-theme-text transition-colors" href={href} target="_blank" rel="noopener noreferrer">:{service.port}</a> : <span className="font-mono uppercase tracking-[0.14em]">internal</span>}</div></div>
}

function StorageBlock({ storage }) {
  const items = [['Models', storage?.models], ['Vector Database', storage?.vector_db], ['Total Data', storage?.total_data]]
  return <div className="space-y-4">{items.map(([label, data]) => <div key={label}><div className="flex items-center justify-between text-sm mb-2"><span className="text-theme-text-muted">{label}</span><span className="text-theme-text">{data?.formatted || 'Unknown'}</span></div><div className="liquid-metal-progress-track h-2 rounded-full overflow-hidden"><div className="h-full liquid-metal-progress-fill rounded-full" style={{ width: `${data?.percent || 0}%` }} /></div></div>)}</div>
}

function ActionButton({ icon: Icon, label, description, onClick }) { return <button onClick={onClick} className="w-full flex items-center gap-4 p-3 rounded-lg transition-colors hover:bg-theme-surface-hover"><Icon size={20} className="text-theme-text-muted" /><div className="text-left"><p className="text-sm text-theme-text font-medium">{label}</p><p className="text-xs text-theme-text-muted">{description}</p></div></button> }

// Settings → sub-page link row. Mirrors the visual shape of ActionButton
// but renders a router <Link> so navigation works without an onClick handler
// and the route gets registered with the SPA's history/scroll restoration.
function SettingsNavRow({ to, icon: Icon, label, description }) {
  return (
    <Link to={to} className="flex items-center gap-4 p-3 rounded-lg transition-colors hover:bg-theme-surface-hover">
      <Icon size={20} className="text-theme-text-muted" />
      <div className="flex-1 text-left">
        <p className="text-sm text-theme-text font-medium">{label}</p>
        <p className="text-xs text-theme-text-muted">{description}</p>
      </div>
      <ChevronRight size={16} className="text-theme-text-muted/60" />
    </Link>
  )
}
