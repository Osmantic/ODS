import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import OpenCodeApp from './OpenCodeApp'
import { isLoopbackBrowser } from '../lib/serviceUrls'

vi.mock('../lib/serviceUrls', async importOriginal => ({
  ...(await importOriginal()),
  isLoopbackBrowser: vi.fn(() => true),
  dashboardHost: vi.fn(() => 'tower2.local'),
}))

const base = {
  id: 'opencode',
  name: 'OpenCode',
  installed: true,
  version: null,
  platform: 'linux',
  port: 3003,
  portInUse: false,
  startSupported: true,
  setupSupported: false,
  setupIssue: null,
  localUrl: 'http://localhost:3003/',
  publicUrl: null,
  progress: null,
}

function app(state, extra = {}) {
  return { ...base, state, running: state === 'running', ...extra }
}

function response(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) })
}

describe('OpenCodeApp', () => {
  let routes
  beforeEach(() => {
    routes = {}
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const key = `${options.method || 'GET'} ${url}`
      const handler = routes[key]
      if (!handler) return response(404, { detail: `unexpected ${key}` })
      return typeof handler === 'function' ? handler() : handler
    }))
  })

  afterEach(() => {
    isLoopbackBrowser.mockReturnValue(true)
    vi.unstubAllGlobals()
  })

  test('opens a running OpenCode directly on the ODS machine', async () => {
    routes['GET /api/apps/opencode'] = () => response(200, app('running', { version: '1.18.32' }))
    render(<OpenCodeApp />)

    const open = await screen.findByRole('link', { name: /Open OpenCode/ })
    expect(open).toHaveAttribute('href', 'http://localhost:3003/')
    expect(open).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('status')).toHaveTextContent('Running · v1.18.32')
    expect(screen.getByText('How to use it')).toBeInTheDocument()
    expect(screen.getByLabelText('terminal attach command')).toHaveTextContent('~/.opencode/bin/opencode attach http://localhost:3003')
  })

  test('explains the SSH tunnel instead of a dead link on another device', async () => {
    isLoopbackBrowser.mockReturnValue(false)
    routes['GET /api/apps/opencode'] = () => response(200, app('running'))
    render(<OpenCodeApp />)

    expect(await screen.findByText(/cannot open directly on this device/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open OpenCode/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText('SSH port-forward command')).toHaveTextContent('ssh -N -L 3003:127.0.0.1:3003 <user>@tower2.local')
  })

  test('starts a stopped OpenCode and then offers to open it', async () => {
    let started = false
    routes['GET /api/apps/opencode'] = () => response(200, app(started ? 'running' : 'stopped'))
    routes['POST /api/apps/opencode/start'] = () => {
      started = true
      return response(200, { opencode: app('running') })
    }
    render(<OpenCodeApp />)

    fireEvent.click(await screen.findByRole('button', { name: /Start OpenCode/ }))

    expect(await screen.findByRole('link', { name: /Open OpenCode/ })).toHaveAttribute('href', 'http://localhost:3003/')
    expect(fetch).toHaveBeenCalledWith('/api/apps/opencode/start', expect.objectContaining({ method: 'POST' }))
  })

  test('reports a start failure from the host', async () => {
    routes['GET /api/apps/opencode'] = () => response(200, app('stopped'))
    routes['POST /api/apps/opencode/start'] = () => response(502, {
      detail: 'Managed OpenCode did not become healthy at http://127.0.0.1:3003/',
      opencode: app('stopped'),
    })
    render(<OpenCodeApp />)

    fireEvent.click(await screen.findByRole('button', { name: /Start OpenCode/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('did not become healthy')
  })

  test('sets OpenCode up on Linux when it was never installed', async () => {
    let setup = false
    routes['GET /api/apps/opencode'] = () => response(200, setup
      ? app('installing', { installed: false, progress: { status: 'pulling', phaseLabel: 'Downloading the reviewed OpenCode release', error: null } })
      : app('not_installed', { installed: false, startSupported: false, setupSupported: true }))
    routes['POST /api/apps/opencode/setup'] = () => {
      setup = true
      return response(202, { opencode: app('installing', { installed: false }) })
    }
    render(<OpenCodeApp />)

    expect(await screen.findByText(/isn’t set up on this ODS machine/)).toBeInTheDocument()
    expect(screen.getByText(/checks its SHA-256 checksum/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Set up OpenCode/ }))

    expect(await screen.findByText('Downloading the reviewed OpenCode release')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Setting up')
  })

  test('shows why setup is unavailable instead of a button', async () => {
    routes['GET /api/apps/opencode'] = () => response(200, app('not_installed', {
      installed: false, startSupported: false, platform: 'darwin',
      setupIssue: 'OpenCode is set up by the ODS installer on this platform. Re-run the installer to repair it.',
    }))
    render(<OpenCodeApp />)

    expect(await screen.findByText(/set up by the ODS installer/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Set up OpenCode/ })).not.toBeInTheDocument()
  })

  test('offers a retry after a failed setup', async () => {
    routes['GET /api/apps/opencode'] = () => response(200, app('not_installed', {
      installed: false, startSupported: false, setupSupported: true,
      progress: { status: 'error', phaseLabel: 'OpenCode setup failed', error: 'OpenCode download or verification failed: OpenCode archive SHA256 mismatch' },
    }))
    render(<OpenCodeApp />)

    expect(await screen.findByText(/Last setup failed: .*SHA256 mismatch/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Try setup again/ })).toBeEnabled()
  })

  test('says so when another program holds the OpenCode port', async () => {
    routes['GET /api/apps/opencode'] = () => response(200, app('stopped', { portInUse: true }))
    render(<OpenCodeApp />)

    expect(await screen.findByText(/another program is using port 3003/)).toBeInTheDocument()
  })

  test('surfaces an unreachable host agent with a retry', async () => {
    routes['GET /api/apps/opencode'] = () => response(503, { detail: 'The ODS host agent is not reachable, so OpenCode cannot be managed right now.' })
    render(<OpenCodeApp />)

    expect(await screen.findByRole('alert')).toHaveTextContent('host agent is not reachable')
    routes['GET /api/apps/opencode'] = () => response(200, app('running'))
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: /Open OpenCode/ })).toBeInTheDocument()
  })
})
