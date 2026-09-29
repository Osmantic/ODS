import { screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Dashboard from './Dashboard'

const services = [
  { name: 'Dashboard (Control Center)', status: 'healthy', port: 3001, uptime: 14400 },
  { name: 'llama-server (LLM Inference)', status: 'healthy', port: 11434, uptime: 14400 },
]

const cloudStatus = {
  services,
  inferenceMode: 'remote',
  inferenceSource: 'remote-provider',
  inference: {
    tokensPerSecond: null,
    lifetimeTokens: null,
    tokenCountMode: 'unavailable',
    contextSize: 8192,
    loadedModel: null,
  },
  // Local hardware telemetry still reports multiple Intel Arc GPUs.
  gpu: {
    name: 'Intel Arc A770',
    gpu_count: 2,
    memoryType: 'discrete',
    vramUsed: 4,
    vramTotal: 16,
    utilization: 30,
    temperature: 60,
    powerDraw: 120,
  },
  cpu: { percent: 20, temp_c: 50, scope: 'host' },
  ram: { used_gb: 8, total_gb: 32, percent: 25, scope: 'host' },
  model: { name: 'remote-model', currentModel: 'remote-model', contextLength: 8192 },
  currentModel: 'remote-model',
  loadedModel: null,
  configuredModel: 'remote-model',
  tier: 'Cloud',
  uptime: 0,
  version: '1.0.0',
}

function installFetchMock() {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/features')) {
      return { ok: true, json: async () => ({ features: [], suggestions: [], summary: { progress: 0 } }) }
    }
    if (String(url).includes('/api/services/resources')) {
      return { ok: true, json: async () => ({ services: [] }) }
    }
    throw new Error(`Unmocked fetch: ${url}`)
  }))
}

describe('Dashboard cloud inference', () => {
  it('labels provider completion throughput as a request average including latency',async()=>{
    render(<Dashboard status={{...cloudStatus,inference:{...cloudStatus.inference,
      tokensPerSecond:12.5,throughputMode:'cloud_request_average',throughputState:'retained'}}} loading={false}/> )
    expect((await screen.findAllByText('Last cloud request · includes latency'))[0]).toBeVisible()
    expect(screen.queryByText('Runtime reading')).toBeNull()
  })
  beforeEach(() => {
    installFetchMock()
    localStorage.clear()
    localStorage.setItem('ods-system-overview-history-v2', '[]')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('hides GPU metrics and multi-GPU summary in full dashboard', async () => {
    render(<Dashboard status={cloudStatus} loading={false} />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))

    expect(screen.queryByText('Intel Arc A770')).not.toBeInTheDocument()
    expect(screen.queryByText('VRAM')).not.toBeInTheDocument()
    expect(screen.queryByText('GPU Temp')).not.toBeInTheDocument()
    expect(screen.queryByText('GPU Power')).not.toBeInTheDocument()
    expect(screen.queryByText(/Multi-GPU System/)).not.toBeInTheDocument()
    expect(screen.queryByText('GPU Monitor')).not.toBeInTheDocument()
    expect(screen.getByText('Cloud API')).toBeInTheDocument()
    expect(screen.getByText('Client CPU')).toBeInTheDocument()
    expect(screen.getByText('Client RAM')).toBeInTheDocument()
    expect(screen.getAllByText('remote-model').length).toBeGreaterThan(0)
    expect(screen.getByText('selected API model')).toBeInTheDocument()
    expect(screen.getByText('API context limit')).toBeInTheDocument()
  })

  it('hides GPU metrics in compact dashboard', async () => {
    render(<Dashboard status={cloudStatus} loading={false} compact />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))

    expect(screen.queryByText('Intel Arc A770')).not.toBeInTheDocument()
    expect(screen.queryByText('VRAM')).not.toBeInTheDocument()
    expect(screen.queryByText('GPU Temp')).not.toBeInTheDocument()
    expect(screen.queryByText('GPU Power')).not.toBeInTheDocument()
    expect(screen.queryByText(/Multi-GPU System/)).not.toBeInTheDocument()
    expect(screen.getByText('Cloud API')).toBeInTheDocument()
    expect(screen.getByText('Client CPU')).toBeInTheDocument()
    expect(screen.getByText('Client RAM')).toBeInTheDocument()
  })

  it('keeps local multi-GPU behavior unchanged', async () => {
    const localStatus = {
      ...cloudStatus,
      inferenceMode: 'local',
      inferenceSource: 'local-runtime',
      tier: 'Prosumer',
      currentModel: 'local-model',
      loadedModel: 'local-model',
      model: { name: 'local-model', currentModel: 'local-model', contextLength: 8192 },
    }
    render(<Dashboard status={localStatus} loading={false} />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))

    expect(screen.getByText('VRAM')).toBeInTheDocument()
    expect(screen.getByText(/Multi-GPU System/)).toBeInTheDocument()
    expect(screen.queryByText('Cloud API')).not.toBeInTheDocument()
    expect(screen.queryByText('Client CPU')).not.toBeInTheDocument()
    expect(screen.queryByText('Client RAM')).not.toBeInTheDocument()
  })

  it('shows em dash for unverified cloud model and not reported subvalue', async () => {
    const unverified = {
      ...cloudStatus,
      inferenceMode: 'cloud',
      inferenceSource: 'cloud-mode',
      currentModel: null,
      loadedModel: null,
      configuredModel: 'configured-only-model',
      model: null,
    }
    render(<Dashboard status={unverified} loading={false} compact />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))

    const modelRow = screen.getByText('Model').closest('.dashboard-metric-row')
    expect(within(modelRow).getByText('—')).toBeVisible()
    expect(within(modelRow).getByText('not reported')).toBeVisible()
    expect(screen.queryByText('configured-only-model')).not.toBeInTheDocument()
  })
})
