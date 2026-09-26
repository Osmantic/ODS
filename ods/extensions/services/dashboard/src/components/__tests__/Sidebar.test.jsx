import { screen, waitFor } from '@testing-library/react'
import { render } from '../../test/test-utils'
import Sidebar from '../Sidebar' // eslint-disable-line no-unused-vars
import { getSidebarExternalLinks } from '../../plugins/registry'

vi.mock('../../plugins/registry', () => ({
  getSidebarNavItems: vi.fn(() => [
    { id: 'dashboard', path: '/', icon: () => <span data-testid="nav-icon">D</span>, label: 'Dashboard' }
  ]),
  getSidebarExternalLinks: vi.fn(() => [])
}))

describe('Sidebar', () => {
  const defaultStatus = {
    services: [
      { name: 'llama-server', status: 'healthy', port: 8080 },
      { name: 'Open WebUI', status: 'healthy', port: 3000 },
      { name: 'n8n', status: 'down', port: 5678 }
    ],
    gpu: { vramUsed: 8, vramTotal: 16 },
    version: '1.0.0',
    tier: 'Standard'
  }

  beforeEach(() => {
    getSidebarExternalLinks.mockReturnValue([])
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    ))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders nav items from plugin registry', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  test('opens profile settings from the workspace footer', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    expect(screen.getByText('Your profile')).toBeInTheDocument()
    expect(screen.getByRole('link',{name:'Edit your profile'})).toHaveAttribute('href','/settings?section=profile')
  })

  test('leaves hardware telemetry on the Dashboard', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    expect(screen.queryByText('VRAM')).not.toBeInTheDocument()
  })

  test('hides nav labels when collapsed', () => {
    render(<Sidebar status={defaultStatus} collapsed={true} onToggle={() => {}} />)
    expect(document.querySelector('aside')).toHaveClass('is-collapsed')
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('title', 'Dashboard')
  })

  test('uses the compact, accessible navigation treatment below the desktop breakpoint', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)

    expect(document.querySelector('aside')).toHaveClass('pixel-sidebar')
    expect(screen.getByText('Dashboard').closest('a')).toHaveClass('pixel-nav-item')
    expect(screen.getByRole('button', { name: /collapse sidebar/i })).toBeInTheDocument()
  })

  test('shows version once in the workspace footer', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    expect(screen.getAllByText('ODS 1.0.0')).toHaveLength(1)
  })

  test('leads a stopped OpenCode to its page instead of a dead Offline entry', () => {
    getSidebarExternalLinks.mockReturnValue([
      {
        key: 'opencode',
        url: 'http://localhost:3003',
        icon: () => <span data-testid="opencode-icon">OC</span>,
        label: 'OpenCode',
        healthy: false,
        alwaysVisible: false,
        visible: true,
        state: 'stopped',
        internalPath: '/apps/opencode',
        stateLabel: 'Stopped',
      },
    ])

    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)

    const entry = screen.getByRole('link', { name: 'OpenCode' })
    expect(entry).toHaveAttribute('href', '/apps/opencode')
    expect(entry).not.toHaveAttribute('target')
    expect(screen.getByText('Stopped')).toBeInTheDocument()
    expect(screen.queryByText('Offline')).not.toBeInTheDocument()
    const applications=screen.getByLabelText('Applications')
    expect(applications).toHaveClass('pixel-nav-item')
    expect(applications.querySelector('svg')).toBeInTheDocument()
  })

  test('opens a running OpenCode in a new tab', () => {
    getSidebarExternalLinks.mockReturnValue([
      {
        key: 'opencode',
        url: 'http://localhost:3003',
        icon: () => <span data-testid="opencode-icon">OC</span>,
        label: 'OpenCode',
        healthy: true,
        visible: true,
        state: 'running',
        internalPath: null,
        stateLabel: null,
      },
    ])

    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)

    const entry = screen.getByRole('link', { name: 'OpenCode' })
    expect(entry).toHaveAttribute('href', 'http://localhost:3003')
    expect(entry).toHaveAttribute('target', '_blank')
  })

  test('lists no OpenCode entry when it was never set up', () => {
    getSidebarExternalLinks.mockReturnValue([
      {
        key: 'opencode',
        url: 'http://localhost:3003',
        icon: () => <span data-testid="opencode-icon">OC</span>,
        label: 'OpenCode',
        healthy: false,
        alwaysVisible: false,
        visible: false,
        state: 'not_installed',
        internalPath: '/apps/opencode',
      },
    ])

    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)

    expect(screen.queryByText('OpenCode')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Applications')).not.toBeInTheDocument()
  })

  test.each([
    ['https://ods.example.test/openclaw?view=chat#recent', 'https://ods.example.test/openclaw?view=chat&token=a%2Bb%2Fc%3F#recent'],
    ['https://ods.example.test/openclaw?token=old&view=chat#recent', 'https://ods.example.test/openclaw?token=a%2Bb%2Fc%3F&view=chat#recent'],
    ['/openclaw?view=chat#recent', `${window.location.origin}/openclaw?view=chat&token=a%2Bb%2Fc%3F#recent`],
  ])('preserves OpenClaw URL state and encodes the token for %s', async (rawUrl, expected) => {
    getSidebarExternalLinks.mockReturnValue([
      {
        key: 'openclaw',
        url: rawUrl,
        icon: () => <span data-testid="openclaw-icon">OC</span>,
        label: 'OpenClaw',
        healthy: true,
      },
    ])
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(
        url === '/api/service-tokens' ? { openclaw: 'a+b/c?' } : [],
      ),
    })))

    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)

    const link = screen.getByText('OpenClaw').closest('a')
    await waitFor(() => {
      expect(link).toHaveAttribute(
        'href',
        expected,
      )
    })
  })
})
