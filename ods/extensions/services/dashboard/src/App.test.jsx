import { screen } from '@testing-library/react'
import { render } from './test/test-utils'
import { render as rtlRender } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom' // eslint-disable-line no-unused-vars
import { ThemeProvider } from './contexts/ThemeContext' // eslint-disable-line no-unused-vars
import App from './App' // eslint-disable-line no-unused-vars
import { useFirstRun } from './hooks/useFirstRun'

vi.mock('./hooks/useSystemStatus', () => ({
  useSystemStatus: vi.fn(() => ({
    status: { gpu: null, services: [], model: null, bootstrap: null, uptime: 0, version: '1.0.0' },
    loading: false,
    error: null
  }))
}))

vi.mock('./hooks/useVersion', () => ({
  useVersion: vi.fn(() => ({
    version: { current: '1.0.0', update_available: false },
    loading: false,
    error: null,
    dismissUpdate: vi.fn()
  }))
}))

// Server-side first-run gating — the hook drives whether SetupWizard mounts.
// Tests below override the mock per case.
vi.mock('./hooks/useFirstRun', () => ({
  useFirstRun: vi.fn(() => ({ firstRun: false, loading: false, error: null, refresh: vi.fn() })),
}))

vi.mock('./plugins/registry', () => ({
  getInternalRoutes: vi.fn(() => []),
  getSidebarNavItems: vi.fn(() => []),
  getSidebarExternalLinks: vi.fn(() => [])
}))

// FirstBoot is lazy-imported in App.jsx and rendered fullscreen when
// firstRun=true. Mock it as a sync component so tests don't need to
// await Suspense.
vi.mock('./pages/FirstBoot', () => ({
  default: ({ onComplete }) => (
    <div data-testid="first-boot">
      <button onClick={onComplete}>Complete</button>
    </div>
  )
}))

vi.mock('./pages/ODSTalk', () => ({
  default: () => <div data-testid="ods-talk">ODS Talk Portal</div>,
}))

vi.mock('./components/SplashScreen', () => ({
  default: ({ onComplete }) => {
    // In tests, immediately complete the splash so App renders normally
    onComplete?.()
    return null
  }
}))

// InstallPromptBanner depends on browser PWA events we don't simulate
// in these App-level tests; render nothing so it doesn't interfere.
vi.mock('./components/InstallPromptBanner', () => ({
  default: () => null,
}))

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    ))
    globalThis.localStorage.removeItem('ods-sidebar-collapsed')
    globalThis.sessionStorage.removeItem('ods-splash-shown')
    useFirstRun.mockReturnValue({ firstRun: false, loading: false, error: null, refresh: vi.fn() })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders without crashing', () => {
    render(<App />)
    expect(document.querySelector('aside')).toBeInTheDocument()
  })

  test('shows FirstBoot when server reports first_run=true', async () => {
    useFirstRun.mockReturnValue({ firstRun: true, loading: false, error: null, refresh: vi.fn() })
    render(<App />)
    // FirstBoot is lazy-loaded under Suspense; await its appearance.
    expect(await screen.findByTestId('first-boot')).toBeInTheDocument()
    // Sidebar must NOT render during onboarding — the wizard owns the screen.
    expect(document.querySelector('aside')).not.toBeInTheDocument()
  })

  test('hides FirstBoot when server reports first_run=false', () => {
    useFirstRun.mockReturnValue({ firstRun: false, loading: false, error: null, refresh: vi.fn() })
    render(<App />)
    expect(screen.queryByTestId('first-boot')).not.toBeInTheDocument()
  })

  test('renders sidebar', () => {
    render(<App />)
    expect(document.querySelector('aside')).toBeInTheDocument()
    expect(document.querySelector('main')).toBeInTheDocument()
  })

  test('uses the compact shell offset below the desktop sidebar breakpoint', () => {
    render(<App />)

    expect(document.querySelector('aside')).toHaveClass('w-20', 'sm:w-64')
    expect(document.querySelector('main')).toHaveClass('ml-20', 'sm:ml-64')
  })

  test('keeps the compact shell offset when the desktop sidebar is collapsed', () => {
    globalThis.localStorage.setItem('ods-sidebar-collapsed', 'true')
    render(<App />)

    expect(document.querySelector('aside')).toHaveClass('w-20')
    expect(document.querySelector('aside')).not.toHaveClass('sm:w-64')
    expect(document.querySelector('main')).toHaveClass('ml-20')
    expect(document.querySelector('main')).not.toHaveClass('sm:ml-64')
  })

  test('renders ODS Talk without dashboard chrome on /talk', async () => {
    rtlRender(
      <MemoryRouter initialEntries={['/talk']}>
        <ThemeProvider>
          <App />
        </ThemeProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('ods-talk')).toBeInTheDocument()
    expect(document.querySelector('aside')).not.toBeInTheDocument()
    expect(globalThis.fetch).not.toHaveBeenCalledWith('/api/auth/admin-session', expect.anything())
  })
})
