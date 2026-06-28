import { screen } from '@testing-library/react'
import { render } from '../../test/test-utils'
import Sidebar from '../Sidebar' // eslint-disable-line no-unused-vars

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

  test('shows service counts in footer', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    // 2 healthy out of 3 deployed (none are not_deployed)
    expect(screen.getByText(/Online: 2\/3/)).toBeInTheDocument()
  })

  test('shows VRAM bar with usage', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    expect(screen.getByText('VRAM')).toBeInTheDocument()
    expect(screen.getByText('8.0/16 GB')).toBeInTheDocument()
  })

  test('hides nav labels when collapsed', () => {
    render(<Sidebar status={defaultStatus} collapsed={true} onToggle={() => {}} />)
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument()
  })

  test('shows version in header', () => {
    render(<Sidebar status={defaultStatus} collapsed={false} onToggle={() => {}} />)
    expect(screen.getByText(/v1\.0\.0/)).toBeInTheDocument()
  })
})
