import { Routes, Route } from 'react-router-dom'
import { useState, useEffect, Suspense, useMemo, useCallback, lazy } from 'react'
import Sidebar from './components/Sidebar'
import InstallPromptBanner from './components/InstallPromptBanner'
import { useSystemStatus } from './hooks/useSystemStatus'
import { useVersion } from './hooks/useVersion'
import { useFirstRun } from './hooks/useFirstRun'
import { useSessionBootstrap } from './hooks/useSessionBootstrap'
import { getInternalRoutes } from './plugins/registry'
import SplashScreen from './components/SplashScreen'

// Phone-first first-boot wizard. Mounted instead of the normal app shell
// when useFirstRun() reports firstRun=true. Lazy-loaded so the wizard
// bundle isn't paid for on every page load after onboarding.
const FirstBoot = lazy(() => import('./pages/FirstBoot'))

function getStorageValue(storage, key) {
  try {
    return storage?.getItem(key)
  } catch {
    return null
  }
}

function setStorageValue(storage, key, value) {
  try {
    storage?.setItem(key, value)
  } catch {
    // Ignore storage failures in private windows or restricted environments.
  }
}

function App() {
  // Auto-mint a dream-session cookie on load so the install owner can
  // reach cookie-gated services (Hermes, future ones) without redeeming
  // a magic link to themselves. No-ops if a valid cookie already exists.
  // See hooks/useSessionBootstrap.js for the full rationale.
  useSessionBootstrap()

  // Show splash only once per browser session — not on every F5 / new tab
  const [splashDone, setSplashDone] = useState(
    () => getStorageValue(globalThis.sessionStorage, 'dream-splash-shown') === '1'
  )
  const { status, loading, error } = useSystemStatus()
  const { version, dismissUpdate } = useVersion()
  // Server-side first-run flag (sourced from /api/setup/status). localStorage
  // was per-browser and gave the wrong answer on re-imaged devices or fresh
  // browsers. The hook returns firstRun=false while it's loading or if the
  // API call fails, so the normal app shell is the safe default.
  const { firstRun, refresh: refreshFirstRun } = useFirstRun()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    return getStorageValue(globalThis.localStorage, 'dream-sidebar-collapsed') === 'true'
  })

  useEffect(() => {
    setStorageValue(globalThis.localStorage, 'dream-sidebar-collapsed', String(sidebarCollapsed))
  }, [sidebarCollapsed])

  const dismissFirstRun = useCallback(() => {
    // SetupWizard's saveConfig has already POSTed /api/setup/complete; we
    // re-fetch the server flag so the next mount sees the new state.
    refreshFirstRun()
  }, [refreshFirstRun])

  const routes = useMemo(() => getInternalRoutes({ status, loading }), [status, loading])
  const handleToggle = useCallback(() => setSidebarCollapsed(c => !c), [])

  // First-boot path: render the FirstBoot SPA fullscreen and lock out the
  // rest of the dashboard. The user can't reach Settings / Extensions / etc.
  // until they've completed onboarding — that simplifies the wizard story
  // (one path, no escape hatches) and prevents half-configured devices
  // from getting halfway-set-up.
  if (firstRun) {
    return (
      <div className="min-h-screen bg-theme-bg text-theme-text">
        {!splashDone && <SplashScreen onComplete={() => {
          setStorageValue(globalThis.sessionStorage, 'dream-splash-shown', '1')
          setSplashDone(true)
        }} />}
        <Suspense fallback={
          <div className="min-h-screen flex items-center justify-center">
            <div className="font-mono text-sm text-theme-accent tracking-widest animate-pulse">DREAM SERVER</div>
          </div>
        }>
          <FirstBoot onComplete={dismissFirstRun} />
        </Suspense>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen bg-theme-bg text-theme-text relative">
      {!splashDone && <SplashScreen onComplete={() => {
        setStorageValue(globalThis.sessionStorage, 'dream-splash-shown', '1')
        setSplashDone(true)
      }} />}
      <Sidebar
        status={status}
        collapsed={sidebarCollapsed}
        onToggle={handleToggle}
      />

      <main className={`dashboard-market-shell flex-1 transition-all duration-200 ${sidebarCollapsed ? 'ml-20' : 'ml-64'}`}>
        {status?.bootstrap?.active && (
          <BootstrapBanner bootstrap={status.bootstrap} />
        )}

        <Suspense fallback={
          <div className="p-8 animate-pulse">
            <div className="h-8 bg-theme-card rounded w-1/3 mb-4" />
            <div className="grid grid-cols-3 gap-6">
              {[...Array(6)].map((_, i) => <div key={i} className="h-40 bg-theme-card rounded-xl" />)}
            </div>
          </div>
        }>
          <Routes>
            {routes.map(route => {
              const Component = route.component
              const props = typeof route.getProps === 'function' ? route.getProps({ status, loading }) : {}
              return (
                <Route
                  key={route.id || route.path}
                  path={route.path}
                  element={<Component {...props} />}
                />
              )
            })}
          </Routes>
        </Suspense>
      </main>

      {/* Smart PWA install nudge — only renders when the user has shown
          enough engagement (3+ visits) AND the browser is willing to
          install. No-op on already-installed PWAs and on browsers that
          can't install (e.g. Firefox desktop). See usePwaInstallPrompt. */}
      <InstallPromptBanner />
    </div>
  )
}

function BootstrapBanner({ bootstrap }) {
  const formatEta = (seconds) => {
    if (!seconds || seconds <= 0) return 'calculating...'
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
    const hours = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    return `${hours}h ${mins}m`
  }

  const formatBytes = (bytes) => {
    if (!bytes) return '0'
    return (bytes / 1e9).toFixed(1)
  }

  return (
    <div className="border-b border-theme-border p-4" style={{ background: `linear-gradient(to right, var(--theme-gradient-from), var(--theme-gradient-to))` }}>
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="w-3 h-3 bg-theme-accent rounded-full animate-pulse" />
            <div>
              <h3 className="text-sm font-semibold text-white">Downloading Full Model</h3>
              <p className="text-xs text-theme-text-secondary">
                Chat now with lightweight model • <span className="text-theme-accent-light">{bootstrap.model}</span> downloading
              </p>
            </div>
          </div>
          <div className="text-right">
            <span className="text-xl font-bold text-theme-accent">{bootstrap.percent?.toFixed(1) || 0}%</span>
            {bootstrap.speedMbps && (
              <p className="text-xs text-theme-text-muted">{bootstrap.speedMbps.toFixed(1)} MB/s</p>
            )}
          </div>
        </div>
        <div className="h-2 bg-theme-border rounded-full overflow-hidden">
          <div
            className="h-full bg-theme-accent rounded-full transition-all duration-500"
            style={{ width: `${bootstrap.percent || 0}%` }}
          />
        </div>
        <p className="text-xs text-theme-text-muted mt-2">
          ETA: {formatEta(bootstrap.eta)} • {formatBytes(bootstrap.bytesDownloaded)} / {formatBytes(bootstrap.bytesTotal)} GB
        </p>
      </div>
    </div>
  )
}

export default App
