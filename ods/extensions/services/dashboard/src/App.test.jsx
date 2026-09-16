import { screen, fireEvent } from '@testing-library/react'
import { render } from './test/test-utils'
import { render as rtlRender } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom' // eslint-disable-line no-unused-vars
import { ThemeProvider } from './contexts/ThemeContext' // eslint-disable-line no-unused-vars
import App from './App' // eslint-disable-line no-unused-vars
import { useFirstRun } from './hooks/useFirstRun'
import { getInternalRoutes } from './plugins/registry'

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
vi.mock('./pages/Pixel', () => ({ default: () => <input aria-label="Portal draft" /> }))
vi.mock('./components/SettingsModal', () => ({ default: () => <input aria-label="Settings draft" /> }))

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
  test('opens legacy Pixel settings in the common utility panel', async () => {
    rtlRender(<MemoryRouter initialEntries={['/pixel/settings']}><ThemeProvider><App /></ThemeProvider></MemoryRouter>)
    expect(await screen.findByLabelText('Settings draft')).toBeVisible()
    expect(screen.getByRole('complementary', {name:'ODS navigation'})).toBeVisible()
    expect(screen.queryByRole('complementary', {name:'Pixel navigation'})).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByLabelText('Portal draft')).toBeVisible()
  })
  test('opens settings inside the workspace and preserves both drafts when collapsed', async () => {
    rtlRender(<MemoryRouter initialEntries={['/settings']}><ThemeProvider><App /></ThemeProvider></MemoryRouter>)
    const chat = await screen.findByLabelText('Portal draft')
    const settings = await screen.findByLabelText('Settings draft')
    fireEvent.change(chat, {target:{value:'Chat draft'}})
    fireEvent.change(settings, {target:{value:'Unsaved settings'}})
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('complementary', {name:'Workspace panel'})).toContainElement(settings)
    fireEvent.click(screen.getByRole('button', {name:'Collapse workspace panel'}))
    expect(settings).not.toBeVisible()
    expect(chat).toBeVisible()
    fireEvent.click(screen.getByRole('button', {name:'Expand workspace panel'}))
    expect(settings).toHaveValue('Unsaved settings')
    fireEvent.click(screen.getByRole('button', {name:'Close workspace panel'}))
    expect(screen.queryByLabelText('Settings draft')).toBeNull()
    expect(chat).toHaveValue('Chat draft')
  })
  test('keeps the portal draft mounted while a panel collapses and closes', async () => {
    getInternalRoutes.mockReturnValue([{id:'dashboard',path:'/dashboard',label:'Dashboard',component:() => <p>Panel readings</p>}])
    rtlRender(<MemoryRouter initialEntries={['/dashboard']}><ThemeProvider><App /></ThemeProvider></MemoryRouter>)
    const input = await screen.findByLabelText('Portal draft')
    fireEvent.change(input, {target:{value:'Keep this message'}})
    expect(await screen.findByText('Panel readings')).toBeVisible()
    fireEvent.click(screen.getByRole('button',{name:'Collapse workspace panel'}))
    expect(screen.getByText('Panel readings')).not.toBeVisible()
    expect(screen.getByLabelText('Portal draft')).toBe(input)
    fireEvent.click(screen.getByRole('button',{name:'Expand workspace panel'}))
    expect(screen.getByText('Panel readings')).toBeVisible()
    fireEvent.click(screen.getByRole('button',{name:'Close workspace panel'}))
    expect(screen.queryByText('Panel readings')).toBeNull()
    expect(screen.getByLabelText('Portal draft')).toBe(input)
    expect(input).toHaveValue('Keep this message')
  })
  beforeEach(() => {
    getInternalRoutes.mockReturnValue([])
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

    expect(document.querySelector('aside')).toHaveClass('pixel-sidebar')
    expect(document.querySelector('main')).toHaveClass('pixel-workspace')
  })

  test('keeps the compact shell offset when the desktop sidebar is collapsed', () => {
    globalThis.localStorage.setItem('ods-sidebar-collapsed', 'true')
    render(<App />)

    expect(document.querySelector('aside')).toHaveClass('is-collapsed')
    expect(document.querySelector('.pixel-app')).toHaveClass('sidebar-collapsed')
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

test.each(['localStorage', 'sessionStorage'])('opens the beta workspace when %s access is denied', async storage => {
  const getter = vi.spyOn(window, storage, 'get').mockImplementation(() => {
    throw new window.DOMException('Storage denied', 'SecurityError')
  })
  try {
    render(<App />)
    expect(await screen.findByLabelText('Portal draft')).toBeVisible()
    fireEvent.click(screen.getByRole('button', {name:'Collapse sidebar'}))
    expect(screen.getByRole('button', {name:'Expand sidebar'})).toBeVisible()
  } finally { getter.mockRestore() }
})
