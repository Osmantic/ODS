import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Dashboard from './Dashboard' // eslint-disable-line no-unused-vars

const services = [
  { name: 'APE (Agent Policy Engine)', status: 'healthy', port: 7890, uptime: 14400 },
  { name: 'Dashboard (Control Center)', status: 'healthy', port: 3001, uptime: 14400 },
  { name: 'Dashboard API (System Status)', status: 'healthy', port: 3002, uptime: 14400 },
  { name: 'LiteLLM (API Gateway)', status: 'healthy', port: 4000, uptime: 14400 },
  { name: 'llama-server (LLM Inference)', status: 'healthy', port: 11434, uptime: 14400 },
  { name: 'Open WebUI (Chat)', status: 'healthy', port: 3000, uptime: 14400 },
  { name: 'Perplexica (Deep Research)', status: 'healthy', port: 3004, uptime: 14400 },
  { name: 'Privacy Shield (PII Protection)', status: 'healthy', port: 8085, uptime: 14400 },
  { name: 'SearXNG (Web Search)', status: 'healthy', port: 8888, uptime: 14400 },
  { name: 'Token Spy (Usage Analytics)', status: 'healthy', port: 3005, uptime: 14400 },
  { name: 'OpenCode (IDE)', status: 'healthy', port: 3003, uptime: 14400 },
]

const baseStatus = {
  services,
  inference: {
    tokensPerSecond: 8,
    lifetimeTokens: 4500,
    contextSize: 32768,
    loadedModel: 'qwen',
  },
  gpu: null,
  model: null,
  bootstrap: null,
  uptime: 0,
  version: '1.0.0',
}

let mockResources
let restartCalls
let restartDeferred

function createDeferred() {
  let resolve
  let reject
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
}

function installFetchMock() {
  restartCalls = []
  restartDeferred = null
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (String(url).includes('/api/features')) {
      return {
        ok: true,
        json: async () => ({ features: [] }),
      }
    }
    if (String(url).includes('/api/services/') && String(url).endsWith('/restart')) {
      restartCalls.push({ url: String(url), options })
      if (restartDeferred) {
        await restartDeferred.promise
      }
      return {
        ok: true,
        json: async () => ({ status: 'ok', service_id: 'ape', action: 'restart' }),
      }
    }
    if (String(url).includes('/api/services/resources')) {
      return {
        ok: true,
        json: async () => mockResources,
      }
    }
    throw new Error(`Unmocked fetch: ${url}`)
  }))
}

async function renderDashboard(status = baseStatus) {
  render(<Dashboard status={status} loading={false} />)
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/services/resources'))
}

describe('Dashboard system overview', () => {
  beforeEach(() => {
    mockResources = {
      services: services.map(service => ({
        id: service.name.toLowerCase().replace(/\([^)]*\)/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''),
        name: service.name,
        type: 'docker',
        restartable: false,
        restart_unavailable_reason: 'No Docker container is declared',
        container: null,
        disk: null,
      })),
    }
    installFetchMock()
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders the system overview panel and both telemetry charts', async () => {
    await renderDashboard()

    expect(screen.getByText('System Overview')).toBeInTheDocument()
    expect(screen.getByText('System Status')).toBeInTheDocument()
    expect(screen.getByText('TOKENS PER SECOND')).toBeInTheDocument()
    expect(screen.getByText('TOKENS GENERATED')).toBeInTheDocument()
    expect(screen.getByText('Live Throughput')).toBeInTheDocument()
    expect(screen.getByText('Accumulated Output')).toBeInTheDocument()
  })

  it('renders the services table with real tab counts and default expansion state', async () => {
    await renderDashboard()

    expect(screen.getByText('Services')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'All (11)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Online (11)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Degraded (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Inactive (0)' })).toBeInTheDocument()
    expect(screen.getByText('APE (Agent Policy Engine)')).toBeInTheDocument()
    expect(screen.getByText('+7 more services')).toBeInTheDocument()

    fireEvent.click(screen.getByText('+7 more services'))
    expect(await screen.findByText('OpenCode (IDE)')).toBeInTheDocument()
    expect(screen.getByText('Show fewer services')).toBeInTheDocument()
  })

  it('filters services by status tab and search input', async () => {
    const mixedStatus = {
      ...baseStatus,
      services: [
        ...services.slice(0, 9),
        { ...services[9], status: 'degraded' },
        { ...services[10], status: 'down', uptime: null },
      ],
    }

    await renderDashboard(mixedStatus)

    expect(screen.getByRole('button', { name: 'Online (9)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Degraded (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Inactive (1)' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Degraded (1)' }))
    expect(screen.getByText('Token Spy (Usage Analytics)')).toBeInTheDocument()
    expect(screen.queryByText('APE (Agent Policy Engine)')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'All (11)' }))
    fireEvent.change(screen.getByLabelText('Search services'), { target: { value: 'litellm' } })
    expect(screen.getByText('LiteLLM (API Gateway)')).toBeInTheDocument()
    expect(screen.queryByText('APE (Agent Policy Engine)')).not.toBeInTheDocument()
  })

  it('uses core service metadata for the headline health summary', async () => {
    mockResources = { services: [] }
    const statusWithOptionalIssue = {
      ...baseStatus,
      services: [
        {
          id: 'llama-server',
          name: 'llama-server (LLM Inference)',
          status: 'healthy',
          required: true,
          impact: 'core',
          port: 11434,
          uptime: 14400,
        },
        {
          id: 'open-webui',
          name: 'Open WebUI (Chat)',
          status: 'healthy',
          required: true,
          impact: 'core',
          port: 3000,
          uptime: 14400,
        },
        {
          id: 'whisper',
          name: 'Whisper (STT)',
          status: 'down',
          required: false,
          impact: 'optional',
          category: 'optional',
          port: 9000,
          uptime: null,
        },
      ],
    }

    await renderDashboard(statusWithOptionalIssue)

    expect(screen.getByText('2/2 core services online.')).toBeInTheDocument()
    expect(screen.getByText('Optional')).toBeInTheDocument()
  })

  it('renders real service CPU and RAM metrics from the resources endpoint', async () => {
    mockResources = {
      services: [
        {
          id: 'ape',
          name: 'APE (Agent Policy Engine)',
          type: 'docker',
          restartable: true,
          restart_unavailable_reason: null,
          container: { cpu_percent: 1.2, memory_used_mb: 128 },
          disk: null,
        },
      ],
    }

    await renderDashboard()

    const row = await screen.findByTestId('service-row-ape')
    expect(within(row).getByText('1.2%')).toBeInTheDocument()
    expect(within(row).getByText('128 MB')).toBeInTheDocument()
  })

  it('renders unavailable service metrics as dashes instead of fake values', async () => {
    await renderDashboard()

    const row = await screen.findByTestId('service-row-ape')
    expect(within(row).getAllByText('—')).toHaveLength(2)
  })

  it('restarts a service from the row actions menu', async () => {
    restartDeferred = createDeferred()
    mockResources = {
      services: [
        {
          id: 'ape',
          name: 'APE (Agent Policy Engine)',
          type: 'docker',
          restartable: true,
          restart_unavailable_reason: null,
          container: null,
          disk: null,
        },
      ],
    }

    await renderDashboard()

    const row = await screen.findByTestId('service-row-ape')
    fireEvent.click(within(row).getByRole('button', { name: 'APE (Agent Policy Engine) actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Restart service/i }))

    const restartingPill = await within(row).findByText('Restarting')
    expect(restartingPill.className).toContain('text-amber-300')
    expect(within(row).queryByText('Online')).not.toBeInTheDocument()

    restartDeferred.resolve()
    expect(await screen.findByText('Restarted')).toBeInTheDocument()
    expect(within(row).getByText('Online')).toBeInTheDocument()
    expect(restartCalls).toHaveLength(1)
    expect(restartCalls[0].url).toBe('/api/services/ape/restart')
    expect(restartCalls[0].options.method).toBe('POST')
  })

  it('closes a service action menu with Escape', async () => {
    mockResources = {
      services: [
        {
          id: 'ape',
          name: 'APE (Agent Policy Engine)',
          type: 'docker',
          restartable: true,
          restart_unavailable_reason: null,
          container: null,
          disk: null,
        },
      ],
    }

    await renderDashboard()

    const row = await screen.findByTestId('service-row-ape')
    fireEvent.click(within(row).getByRole('button', { name: 'APE (Agent Policy Engine) actions' }))
    expect(screen.getByRole('menuitem', { name: /Restart service/i })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menuitem', { name: /Restart service/i })).not.toBeInTheDocument()
  })

  it('does not offer restart for host-level services', async () => {
    mockResources = {
      services: [
        {
          id: 'opencode',
          name: 'OpenCode (IDE)',
          type: 'host-systemd',
          restartable: false,
          restart_unavailable_reason: 'Host-level service; restart outside Docker',
          container: null,
          disk: null,
        },
      ],
    }

    await renderDashboard()
    fireEvent.click(screen.getByText('+7 more services'))

    const row = await screen.findByTestId('service-row-opencode')
    expect(within(row).queryByRole('button', { name: 'OpenCode (IDE) actions' })).not.toBeInTheDocument()
  })

  it('switches the local overview range when a tab is clicked', async () => {
    await renderDashboard()

    expect(screen.getByRole('button', { name: '1H' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '6H' }))
    expect(screen.getByRole('button', { name: '6H' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '1H' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('renders without inference telemetry', async () => {
    const statusWithoutInference = { ...baseStatus, inference: undefined }

    await renderDashboard(statusWithoutInference)

    expect(screen.getByText('System Overview')).toBeInTheDocument()
    expect(screen.getByText('TOKENS PER SECOND')).toBeInTheDocument()
    expect(screen.getByText('TOKENS GENERATED')).toBeInTheDocument()
  })

  it('shows a red downward delta when real throughput history drops', async () => {
    const now = Date.now()
    localStorage.setItem('dream-system-overview-history-v1', JSON.stringify([
      { t: now - 300000, tokensPerSecond: 20, totalTokens: 4000 },
      { t: now - 60000, tokensPerSecond: 10, totalTokens: 4500 },
    ]))

    await renderDashboard({ ...baseStatus, inference: { ...baseStatus.inference, tokensPerSecond: 10 } })

    const delta = screen.getByText('↓ 50.0%')
    expect(delta).toBeInTheDocument()
    expect(delta.className).toContain('text-red-400')
  })
})
