import { screen } from '@testing-library/react'
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

  test('keeps an always-visible OpenCode launcher in the default application list', () => {
    getSidebarExternalLinks.mockReturnValueOnce([
      {
        key: 'opencode',
        url: 'http://localhost:3003',
        icon: () => <span data-testid="opencode-icon">OC</span>,
        label: 'OpenCode',
        healthy: false,
        alwaysVisible: true,
      },
    ])

    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)

    expect(screen.getByText('OpenCode')).toBeInTheDocument()
    expect(screen.getByText('Offline')).toBeInTheDocument()
    expect(screen.getByText('OpenCode').closest('a')).not.toHaveAttribute('href')
  })
})
