import { NavLink } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Palette
} from 'lucide-react'
import { getSidebarExternalLinks, getSidebarNavItems } from '../plugins/registry'
import { useTheme } from '../contexts/ThemeContext'

// Derive external service URLs from current host
const getExternalUrl = (port) =>
  typeof window !== 'undefined'
    ? `http://${window.location.hostname}:${port}`
    : `http://localhost:${port}`

export default function Sidebar({ status, collapsed, onToggle }) {
  const { theme, cycleTheme, labels } = useTheme() // eslint-disable-line no-unused-vars -- theme switcher temporarily hidden
  const [serviceTokens, setServiceTokens] = useState({})
  const [apiLinks, setApiLinks] = useState([])
  const [showAllQuickLinks, setShowAllQuickLinks] = useState(false)

  useEffect(() => {
    fetch('/api/service-tokens')
      .then(r => r.ok ? r.json() : {})
      .then(setServiceTokens)
      .catch(() => {})

    fetch('/api/external-links')
      .then(r => r.ok ? r.json() : [])
      .then(setApiLinks)
      .catch(() => {})
  }, [])

  const navItems = useMemo(
    () => getSidebarNavItems({ status }),
    [status]
  )

  // Compute external links with auto-auth tokens (e.g. OpenClaw ?token=xxx)
  const externalLinks = useMemo(() => {
    const links = getSidebarExternalLinks({ status, getExternalUrl, apiLinks })
    return links.map(link => {
      if (link.key === 'openclaw' && serviceTokens.openclaw) {
        return { ...link, url: `${link.url}/?token=${serviceTokens.openclaw}` }
      }
      return link
    })
  }, [status, serviceTokens, apiLinks])

  const visibleExternalLinks = useMemo(() => {
    return showAllQuickLinks ? externalLinks : externalLinks.filter(link => link.healthy)
  }, [externalLinks, showAllQuickLinks])

  // Service counts with degraded nuance
  const services = status?.services || []
  const deployed = services.filter(s => s.status !== 'not_deployed')
  const onlineCount = deployed.filter(s => s.status === 'healthy' || s.status === 'degraded').length
  const degradedCount = deployed.filter(s => s.status === 'degraded').length
  const totalCount = deployed.length

  // Memory bar: use unified (RAM) stats on APUs, VRAM on discrete
  const isUnified = status?.gpu?.memoryType === 'unified'
  const memPct = isUnified
    ? (status?.ram?.percent || 0)
    : status?.gpu?.vramTotal > 0
      ? (status.gpu.vramUsed / status.gpu.vramTotal) * 100
      : 0
  const memUsed = isUnified ? (status?.ram?.used_gb || 0) : (status?.gpu?.vramUsed || 0)
  const memTotal = isUnified ? (status?.ram?.total_gb || 0) : (status?.gpu?.vramTotal || 0)
  const memLabel = isUnified ? 'Memory' : 'VRAM'
  const memFillClass = memPct > 90
    ? 'liquid-metal-progress-fill liquid-metal-progress-fill--danger'
    : memPct > 75
      ? 'liquid-metal-progress-fill liquid-metal-progress-fill--warn'
      : 'liquid-metal-progress-fill'

  // Footer status color
  const footerColor = degradedCount > 0
    ? 'text-yellow-500'
    : onlineCount === totalCount
      ? 'text-green-500'
      : totalCount > 0
        ? 'text-yellow-500'
        : 'text-theme-text-muted'

  return (
    <aside
      className={`fixed left-0 top-0 h-screen ${collapsed ? 'w-20' : 'w-64'} flex flex-col transition-all duration-200`}
      style={{
        background: `var(--sidebar-bg-glow), var(--sidebar-bg)`,
        borderRight: '1px solid var(--sidebar-border)',
        boxShadow: 'inset -1px 0 0 rgba(255,255,255,0.04)',
      }}
    >
      {/* Logo */}
      <div className="px-4 pt-6 pb-5 border-b overflow-hidden" style={{ borderColor: 'var(--sidebar-border)' }}>
        {collapsed ? (
          <div className="flex flex-col items-center">
            <div
              className="flex h-11 w-11 items-center justify-center rounded-xl border"
              style={{
                background: `linear-gradient(180deg, color-mix(in srgb, var(--sidebar-accent) 18%, transparent), color-mix(in srgb, var(--sidebar-accent) 6%, transparent))`,
                borderColor: `color-mix(in srgb, var(--sidebar-accent-soft) 24%, transparent)`,
              }}
            >
              <span className="text-lg font-black tracking-tight" style={{ color: 'var(--sidebar-accent-soft)' }}>DS</span>
            </div>
            <p className="text-[8px] font-mono mt-2 tracking-[0.18em] uppercase" style={{ color: 'var(--sidebar-text-muted)' }}>
              v{status?.version || '...'}
            </p>
          </div>
        ) : (
          <>
            <pre
              aria-hidden="true"
              className="dream-logo-liquid text-[7.5px] leading-[8px] font-mono whitespace-pre select-none"
            >{`    ____
   / __ \\ _____ ___   ____ _ ____ ___
  / / / // ___// _ \\ / __ \`// __ \`__ \\
 / /_/ // /   /  __// /_/ // / / / / /
/_____//_/    \\___/ \\__,_//_/ /_/ /_/
    _____
   / ___/ ___   _____ _   __ ___   _____
   \\__ \\ / _ \\ / ___/| | / // _ \\ / ___/
  ___/ //  __// /    | |/ //  __// /
 /____/ \\___//_/     |___/ \\___//_/`}</pre>
            <p className="text-[8px] font-mono tracking-[0.28em] mt-2.5 uppercase" style={{ color: 'var(--sidebar-accent-soft)' }}>
              LOCAL AI // SOVEREIGN INTELLIGENCE
            </p>
            <p className="text-[10px] mt-1" style={{ color: 'var(--sidebar-text-secondary)' }}>
              {status?.tier || 'Minimal'} • v{status?.version || '...'}
            </p>
          </>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 overflow-y-auto overflow-x-hidden">
        <ul className="space-y-1.5">
          {navItems.map(({ path, icon: Icon, label }) => (
            <li key={path}>
              <NavLink
                to={path}
                end
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  `flex items-center ${collapsed ? 'justify-center' : ''} gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                    isActive
                      ? 'liquid-metal-nav text-white shadow-lg'
                      : 'text-theme-text-muted hover:text-theme-text'
                  }`
                }
                style={({ isActive }) => isActive
                  ? {
                    border: '1px solid var(--sidebar-active-border)',
                    boxShadow: 'var(--sidebar-active-shadow)',
                  }
                  : {
                    background: 'transparent',
                  }
                }
              >
                <Icon size={20} />
                {!collapsed && <span>{label}</span>}
              </NavLink>
            </li>
          ))}
        </ul>

        {/* External Links — hidden when collapsed */}
        {!collapsed && (
          <div className="mt-4 pt-4 border-t" style={{ borderColor: 'var(--sidebar-border)' }}>
            <div className="mb-2 flex items-center justify-between px-3">
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em]" style={{ color: 'var(--sidebar-accent-soft)' }}>
                Quick Links
              </p>
              {externalLinks.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAllQuickLinks(current => !current)}
                  className="text-[9px] font-mono uppercase tracking-[0.18em] transition-colors hover:text-theme-text"
                  style={{ color: 'var(--sidebar-accent-soft)' }}
                >
                  {showAllQuickLinks ? 'Show open' : 'View all'}
                </button>
              )}
            </div>
            <ul className="space-y-0">
              {visibleExternalLinks.map(({ key, url, icon: Icon, label, healthy }) => (
                <li key={key}>
                  <a
                    href={healthy ? url : undefined}
                    onClick={(e) => { if (!healthy) e.preventDefault() }}
                    target={healthy ? '_blank' : undefined}
                    rel={healthy ? 'noopener noreferrer' : undefined}
                    className={`flex items-start gap-2.5 px-3 py-1.5 rounded-lg transition-colors ${
                      healthy
                        ? 'hover:bg-white/[0.03]'
                        : 'text-theme-text-muted/40 cursor-not-allowed'
                    }`}
                    style={healthy ? { color: 'var(--sidebar-text)' } : undefined}
                  >
                    <span className="mt-0.5" style={{ color: healthy ? 'var(--sidebar-accent-soft)' : 'var(--sidebar-inactive)' }}>
                      <Icon size={15} />
                    </span>
                    <span className="text-[12px] leading-4">{label}</span>
                    <span
                      className="ml-auto text-[9px] font-mono uppercase tracking-[0.18em]"
                      style={{ color: healthy ? 'var(--sidebar-accent-soft)' : 'var(--sidebar-inactive)' }}
                    >
                      {healthy ? 'OPEN' : '—'}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </nav>

      {/* Theme + Toggle buttons */}
      {/* Theme switcher hidden until Lemonade/Light/Arctic themes are polished for liquid metal design.
          To restore: uncomment the Palette button and label below. */}
      <div className={`mb-2 flex ${collapsed ? 'mx-2 flex-col items-center gap-2' : 'mx-4 items-center gap-1'}`}>
        {/* <button
          onClick={cycleTheme}
          className="flex items-center justify-center p-2 rounded-lg text-theme-text-muted hover:text-theme-text transition-colors"
          title={`Theme: ${labels[theme]} (click to cycle)`}
          style={{ background: 'var(--sidebar-hover-bg)' }}
        >
          <Palette size={18} />
        </button>
        {!collapsed && (
          <span className="text-xs" style={{ color: 'var(--sidebar-label)' }}>{labels[theme]}</span>
        )} */}
        <button
          onClick={onToggle}
          className={`${collapsed ? '' : 'ml-auto'} flex items-center justify-center p-2 rounded-lg text-theme-text-muted hover:text-theme-text transition-colors`}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={{ background: 'var(--sidebar-hover-bg)' }}
        >
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </button>
      </div>

      {/* Status Footer */}
      <div className="p-4 border-t" style={{ borderColor: 'var(--sidebar-border)' }}>
        {!collapsed && (
          <div className="flex items-center justify-between text-sm mb-2">
            <span className="text-theme-text-muted">Services</span>
            <span className={footerColor}>
              {degradedCount > 0
                ? `Online: ${onlineCount}/${totalCount} · ${degradedCount} degraded`
                : `Online: ${onlineCount}/${totalCount}`
              }
            </span>
          </div>
        )}
        {(status?.gpu || (isUnified && status?.ram)) && (
          <div>
            {!collapsed && (
              <div className="flex items-center justify-between text-xs text-theme-text-muted mb-1">
                <span>{memLabel}</span>
                <span className="font-mono">{memUsed.toFixed ? memUsed.toFixed(1) : memUsed}/{memTotal.toFixed ? memTotal.toFixed(0) : memTotal} GB</span>
              </div>
            )}
            <div
              className="liquid-metal-progress-track h-1.5 rounded-full overflow-hidden"
              title={collapsed ? `${memLabel}: ${memUsed.toFixed ? memUsed.toFixed(1) : memUsed}/${memTotal.toFixed ? memTotal.toFixed(0) : memTotal} GB` : undefined}
            >
              <div
                className={`h-full rounded-full transition-all ${memFillClass}`}
                style={{ width: `${Math.min(memPct, 100)}%` }}
              />
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
