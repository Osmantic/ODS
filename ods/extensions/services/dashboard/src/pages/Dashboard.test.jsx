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
    tokenCountMode: 'cumulative',
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
let mockFeatures
let mockFeatureSuggestions
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
        json: async () => ({
          features: mockFeatures,
          suggestions: mockFeatureSuggestions,
          summary: { progress: 0 },
        }),
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
    document.documentElement.dataset.theme = 'light'
    mockFeatures = []
    mockFeatureSuggestions = []
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
    localStorage.setItem('ods-system-overview-history-v2', '[]')
  })

  afterEach(() => {
    delete document.documentElement.dataset.theme
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders the system overview panel and both telemetry charts', async () => {
    await renderDashboard()

    expect(screen.getByText('System Overview')).toBeInTheDocument()
    expect(screen.getByText('System Status')).toBeInTheDocument()
    expect(screen.getByText('TOKENS PER SECOND')).toBeInTheDocument()
    expect(screen.getByText('TOKENS GENERATED')).toBeInTheDocument()
    expect(screen.getAllByText('Runtime reading').length).toBeGreaterThan(0)
    expect(screen.getByText('Accumulated Output')).toBeInTheDocument()
  })

  it.each([[8.25, '8.3 tok/s'], [0, '0.0 tok/s'], [null, '—'], [undefined, '—']])('shows a real compact throughput reading for %s', async (tokensPerSecond, expected) => {
    render(<Dashboard compact status={{...baseStatus, inference:{...baseStatus.inference, tokensPerSecond}}} loading={false}/>)
    const row = screen.getByText('Tokens / second').closest('.dashboard-metric-row')
    expect(within(row).getByText(expected)).toBeVisible()
    expect(within(row).getByText(tokensPerSecond == null ? 'Telemetry unavailable' : 'Runtime reading')).toBeVisible()
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))
  })

  it.each([[0, '0.0'], [null, '—'], [undefined, '—']])('keeps full throughput reading %s distinct from unavailable history', async (tokensPerSecond, expected) => {
    await renderDashboard({...baseStatus, inference:{...baseStatus.inference, tokensPerSecond, lifetimeTokens:null}})
    const throughput = screen.getByRole('img', {name:'TOKENS PER SECOND chart'}).parentElement
    const generated = screen.getByRole('img', {name:'TOKENS GENERATED chart'}).parentElement
    expect(within(throughput).getByText(expected)).toBeVisible()
    expect(within(generated).getByText('—')).toBeVisible()
    const samples = JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
    expect(samples.at(-1)).toMatchObject({tokensPerSecond:tokensPerSecond ?? null,totalTokens:null})
    if(tokensPerSecond == null) expect(within(throughput).queryByText(/↓ 100/)).toBeNull()
  })

  it('does not reuse legacy history that represented unavailable telemetry as zero', async () => {
    localStorage.setItem('ods-system-overview-history-v1', JSON.stringify([
      {t:Date.now()-6000,tokensPerSecond:0,totalTokens:0},
      {t:Date.now()-3000,tokensPerSecond:0,totalTokens:0},
    ]))
    await renderDashboard({...baseStatus,inference:undefined})
    expect(screen.getByRole('img',{name:'TOKENS PER SECOND chart'}).querySelector('path')).toBeNull()
  })

  it('breaks the throughput chart across unavailable readings without losing valid counts', async () => {
    const now=Date.now()
    localStorage.setItem('ods-system-overview-history-v2', JSON.stringify([8,8,null,8,8].map((value,index)=>({
      t:now-30000+index*5000,tokensPerSecond:value,totalTokens:100+index,tokenCountMode:'cumulative',model:'qwen',
    }))))
    await renderDashboard()
    const chart=screen.getByRole('img',{name:'TOKENS PER SECOND chart'})
    const line=chart.querySelector('path[fill="none"]')
    expect(line.getAttribute('d').match(/M /g)).toHaveLength(2)
    expect(line.getAttribute('d')).not.toMatch(/NaN/)
    const generated=screen.getByRole('img',{name:'TOKENS GENERATED chart'})
    expect(generated.querySelector('path[fill="none"]').getAttribute('d').match(/M /g)).toHaveLength(1)
  })

  it('records fresh identical telemetry polls so an idle measured zero develops a history', async () => {
    const clock=vi.spyOn(Date,'now').mockReturnValue(1800000000000)
    const inference={...baseStatus.inference,tokensPerSecond:0}
    const view=render(<Dashboard status={{...baseStatus,inference}} loading={false}/>)
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
    clock.mockReturnValue(1800000005000)
    view.rerender(<Dashboard status={{...baseStatus,inference:{...inference}}} loading={false}/>)
    const samples=JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
    expect(samples.slice(-2).map(sample=>[sample.t,sample.tokensPerSecond])).toEqual([[1800000000000,0],[1800000005000,0]])
  })

  it('shows missing CPU utilization and an available unified GPU temperature truthfully', async () => {
    render(<Dashboard compact status={{...baseStatus,cpu:{percent:null},gpu:{name:'AMD Radeon 8060S',memoryType:'unified',utilization:null,temperature:72}}} loading={false}/>)
    const cpu=screen.getByText('CPU').closest('.dashboard-metric-row')
    expect(within(cpu).getByText('—')).toBeVisible()
    expect(within(cpu).getByText('telemetry unavailable')).toBeVisible()
    const thermal=screen.getByText('GPU Temp').closest('.dashboard-metric-row')
    expect(within(thermal).getByText('72°C')).toBeVisible()
    expect(within(thermal).getByText('warm')).toBeVisible()
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
  })

  it.each([false,true])('labels preserved readings stale in compact=%s without appending a live sample', async compact => {
    const inference={...baseStatus.inference}
    const status={...baseStatus,inference,clientTelemetry:{sampledAt:1800000000000,stale:false}}
    const view=render(<Dashboard status={status} loading={false} compact={compact}/>)
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
    const history=localStorage.getItem('ods-system-overview-history-v2')
    view.rerender(<Dashboard status={{...status,clientTelemetry:{...status.clientTelemetry,stale:true}}} loading={false} compact={compact}/>)
    expect(screen.getByRole('status',{name:'Telemetry freshness'})).toHaveTextContent('last known readings received at')
    expect(screen.getByRole('status',{name:'Telemetry freshness'}).querySelector('time')).toHaveAttribute('dateTime','2027-01-15T08:00:00.000Z')
    expect(localStorage.getItem('ods-system-overview-history-v2')).toBe(history)
    if(compact) expect(screen.getByText('Last known rate · telemetry unavailable')).toBeVisible()
    else expect(screen.getAllByText('Last known rate · telemetry unavailable').length).toBeGreaterThan(0)
    view.rerender(<Dashboard status={{...status,inference:{...inference}}} loading={false} compact={compact}/>)
    expect(screen.queryByRole('status',{name:'Telemetry freshness'})).toBeNull()
  })

  it.each([['generation_interval','Generation interval'],['latest_completion','Latest completion'],['live_output_interval','Live output interval']])('labels %s throughput by its actual measurement window', async (throughputMode,label) => {
    await renderDashboard({...baseStatus,inference:{...baseStatus.inference,throughputMode}})
    expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    expect(screen.queryByText('Live Throughput')).toBeNull()
  })

  it.each([['host','Host'],['wsl','WSL'],['vm','Virtual machine'],['container','Container'],['unknown','Scope unavailable']])('labels CPU and RAM scope %s from the API', async (scope,label) => {
    render(<Dashboard compact status={{...baseStatus,cpu:{percent:38,scope},ram:{used_gb:4,total_gb:8,percent:50,scope}}} loading={false}/>)
    expect(screen.getByText(`${label} · utilization`)).toBeVisible()
    expect(screen.getByText(`of 8 GB · ${label}`)).toBeVisible()
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
  })

  it('retains the last run rate without inventing fresh throughput samples between runs', async () => {
    const clock=vi.spyOn(Date,'now').mockReturnValue(1800000000000)
    const inference={...baseStatus.inference,tokensPerSecond:24.8,throughputState:'measured',throughputSampledAt:1800000000,throughputMode:'generation_interval'}
    const view=render(<Dashboard status={{...baseStatus,inference}} loading={false}/>)
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
    const history=localStorage.getItem('ods-system-overview-history-v2')
    clock.mockReturnValue(1800000005000)
    view.rerender(<Dashboard status={{...baseStatus,inference:{...inference,throughputState:'retained'}}} loading={false}/>)
    expect(screen.getAllByText('Last run').length).toBeGreaterThan(0)
    expect(screen.getByText('24.8')).toBeVisible()
    expect(localStorage.getItem('ods-system-overview-history-v2')).toBe(history)
    clock.mockReturnValue(1800000010000)
    view.rerender(<Dashboard status={{...baseStatus,inference:{...inference,throughputSampledAt:1800000010}}} loading={false}/>)
    expect(JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))).toHaveLength(2)
  })

  it('keeps a held rate visible but marks failed inference telemetry unavailable and does not record the held value again', async () => {
    const clock=vi.spyOn(Date,'now').mockReturnValue(1800000000000)
    const inference={...baseStatus.inference,tokensPerSecond:24.8,throughputState:'measured',throughputSampledAt:1800000000}
    const view=render(<Dashboard status={{...baseStatus,inference}} loading={false}/>)
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
    clock.mockReturnValue(1800000005000)
    view.rerender(<Dashboard status={{...baseStatus,inference:{...inference,throughputState:'unavailable'}}} loading={false}/>)
    expect(screen.getByText('24.8')).toBeVisible()
    expect(screen.getAllByText('Last known rate · telemetry unavailable').length).toBeGreaterThan(0)
    const history=JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
    expect(history.map(row=>row.tokensPerSecond)).toEqual([24.8,null])
  })

  it('never carries previous model throughput history into a new model', async () => {
    const view=render(<Dashboard status={baseStatus} loading={false}/>)
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
    view.rerender(<Dashboard status={{...baseStatus,inference:{...baseStatus.inference,loadedModel:'new-model',tokensPerSecond:null}}} loading={false}/>)
    const history=JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
    expect(history.every(row=>row.model==='new-model' && row.tokensPerSecond===null)).toBe(true)
    expect(screen.getByRole('img',{name:'TOKENS PER SECOND chart'}).querySelector('path')).toBeNull()
  })

  it('keeps an unavailable unified GPU thermal sensor visible without inventing a temperature', async () => {
    render(<Dashboard compact status={{...baseStatus,gpu:{name:'Apple M4',memoryType:'unified',utilization:null,temperature:null}}} loading={false}/>)
    const thermal=screen.getByText('GPU Temp').closest('.dashboard-metric-row')
    expect(within(thermal).getByText('—')).toBeVisible()
    expect(within(thermal).getByText('telemetry unavailable')).toBeVisible()
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
  })

  it('binds held throughput to its measured model during a discovery outage rather than a configured fallback', async () => {
    const clock=vi.spyOn(Date,'now').mockReturnValue(1800000000000)
    const inference={...baseStatus.inference,tokensPerSecond:24.8,throughputState:'measured',throughputSampledAt:1800000000,throughputModel:'actual-owner'}
    const view=render(<Dashboard status={{...baseStatus,inference}} loading={false}/>)
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
    clock.mockReturnValue(1800000005000)
    view.rerender(<Dashboard status={{...baseStatus,inference:{...inference,loadedModel:'configured-fallback',throughputState:'unavailable'}}} loading={false}/>)
    let history=JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
    expect(history.map(row=>[row.model,row.tokensPerSecond])).toEqual([['actual-owner',24.8],['actual-owner',null]])
    clock.mockReturnValue(1800000010000)
    view.rerender(<Dashboard status={{...baseStatus,inference:{...inference,loadedModel:'configured-fallback',throughputModel:'new-owner',throughputSampledAt:1800000010}}} loading={false}/>)
    history=JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
    expect(history).toHaveLength(1)
    expect(history[0]).toMatchObject({model:'new-owner',tokensPerSecond:24.8})
  })

  it.each([[73,'73°C'],[0,'0°C'],[null,'—'],[undefined,'—']])('shows CPU thermal reading %s without substituting GPU or system temperature', async (temp_c,expected) => {
    render(<Dashboard compact status={{...baseStatus,cpu:{percent:38,temp_c},gpu:{name:'GPU',temperature:55,memoryType:'discrete'}}} loading={false}/>)
    const thermal=screen.getByText('CPU Temp').closest('.dashboard-metric-row')
    expect(within(thermal).getByText(expected)).toBeVisible()
    expect(within(thermal).getByText(temp_c == null ? 'telemetry unavailable' : 'sensor reading')).toBeVisible()
    expect(within(thermal).queryByText('55°C')).toBeNull()
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/features'))
  })

  it('uses theme-responsive surfaces instead of fixed dark dashboard panels', async () => {
    await renderDashboard()

    const overviewPanel = screen.getByText('System Overview').closest('section')
    const servicesPanel = screen.getByText('Services').closest('section')

    expect(overviewPanel.getAttribute('style')).toContain('background: var(--tech-panel-fill)')
    expect(overviewPanel.getAttribute('style')).toContain('border-color: var(--tech-panel-border)')
    expect(servicesPanel.getAttribute('style')).toContain('background: var(--tech-panel-fill)')
    expect(servicesPanel.getAttribute('style')).toContain('border-color: var(--tech-panel-border)')
    expect(overviewPanel.getAttribute('style')).not.toContain('rgba(10, 10, 18')
  })

  it('renders unavailable host GPU counters as unavailable instead of zero', async () => {
    await renderDashboard({
      ...baseStatus,
      gpu: {
        name: 'AMD Radeon RX 9070 XT',
        vramUsed: null,
        vramTotal: 16,
        utilization: null,
        temperature: null,
        memoryType: 'discrete',
      },
    })

    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText('Radeon RX 9070 XT')).toBeInTheDocument()
    expect(screen.getByText('of 16 GB')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('renders the real discrete VRAM reading independently of system RAM in the overview', async () => {
    render(<Dashboard compact status={{ ...baseStatus, gpu: { name:'AMD Radeon RX 9070 XT', memoryType:'discrete', vramUsed:7.8, vramTotal:15.8, utilization:16 }, ram:{used_gb:41,total_gb:96,percent:43} }} loading={false} />)
    const row = screen.getByText('VRAM').closest('.dashboard-metric-row')
    expect(within(row).getByText('7.8 GB')).toBeVisible()
    expect(within(row).getByText('of 15.8 GB')).toBeVisible()
    expect(within(row).queryByText('41 GB')).toBeNull()
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/features'))
  })

  it('does not identify an AMD unified GPU as Apple Silicon', async () => {
    await renderDashboard({ ...baseStatus, gpu:{ name:'AMD Radeon 8060S Graphics', memoryType:'unified', utilization:61, vramUsed:9, vramTotal:96 }, ram:{used_gb:40,total_gb:128,percent:31} })
    expect(screen.getByText('GPU memory')).toBeVisible()
    expect(screen.getByText('9.0 GB')).toBeVisible()
    expect(screen.queryByText('Apple Silicon')).toBeNull()
  })

  it('labels Lemonade output as the latest completion instead of a cumulative total', async () => {
    await renderDashboard({
      ...baseStatus,
      inference: {
        ...baseStatus.inference,
        lifetimeTokens: 36,
        tokenCountMode: 'latest_completion',
      },
    })

    expect(screen.getByText('OUTPUT TOKENS')).toBeInTheDocument()
    expect(screen.getByText('Latest Completion')).toBeInTheDocument()
    expect(screen.queryByText('Accumulated Output')).not.toBeInTheDocument()
  })

  it('does not mix cumulative history into latest-completion charts', async () => {
    localStorage.setItem('ods-system-overview-history-v2', JSON.stringify([{
      t: Date.now() - 1000,
      tokensPerSecond: 20, model:'qwen',
      totalTokens: 900000,
      tokenCountMode: 'cumulative',
    }]))

    await renderDashboard({
      ...baseStatus,
      inference: {
        ...baseStatus.inference,
        lifetimeTokens: 36,
        tokenCountMode: 'latest_completion',
      },
    })

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('ods-system-overview-history-v2'))
      expect(stored).toHaveLength(1)
      expect(stored[0]).toMatchObject({
        totalTokens: 36,
        tokenCountMode: 'latest_completion',
      })
    })
  })

  it('does not render feature discovery suggestions as a dashboard home banner', async () => {
    mockFeatureSuggestions = [{
      featureId: 'lan-web',
      name: 'LAN web entry',
      message: 'Your hardware can run LAN web entry. Enable it?',
      action: 'Enable LAN web entry',
      setupTime: 'Ready',
    }]

    await renderDashboard()

    expect(screen.queryByText(/Your hardware can run LAN web entry/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Enable LAN web entry/i })).not.toBeInTheDocument()
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

  it('renders service-level model swap safety in the services table', async () => {
    const statusWithLlmContract = {
      ...baseStatus,
      services: [
        {
          ...services[0],
          llm: {
            consumes: true,
            route: 'direct',
            pinning: 'none',
            swap_safe: false,
            swap_safe_reason: 'Direct model route without a declared refresh path.',
          },
        },
        ...services.slice(1),
      ],
    }

    await renderDashboard(statusWithLlmContract)

    expect(screen.getByText('Not swap-safe')).toBeInTheDocument()
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

  it('launches feature cards to explicit user-facing targets', async () => {
    mockFeatures = [
      {
        id: 'chat',
        name: 'AI Chat',
        description: 'Chat with a local model',
        icon: 'MessageSquare',
        status: 'enabled',
        launch: { type: 'service', service: 'open-webui' },
        requirements: { servicesMissing: [] },
      },
      {
        id: 'hermes-agent',
        name: 'Hermes Agent',
        description: 'Advanced Hermes agent console',
        icon: 'MessageSquare',
        status: 'enabled',
        launch: { type: 'service', service: 'hermes-proxy' },
        requirements: { servicesAll: ['hermes', 'hermes-proxy', 'dashboard-api'], servicesAny: ['llama-server', 'litellm'], servicesMissing: [] },
      },
      {
        id: 'hermes-sso',
        name: 'Hermes Single Sign-On',
        description: 'Manage Hermes access',
        icon: 'MessageSquare',
        status: 'enabled',
        launch: { type: 'internal', path: '/invites' },
        requirements: { servicesAll: ['hermes', 'hermes-proxy', 'dashboard-api'], servicesMissing: [] },
      },
      {
        id: 'remote-access',
        name: 'Remote Access',
        description: 'Tailscale remote access status',
        icon: 'MessageSquare',
        status: 'enabled',
        launch: { type: 'none' },
        requirements: { servicesMissing: [] },
      },
    ]

    const statusWithLaunchTargets = {
      ...baseStatus,
      services: [
        { id: 'llama-server', name: 'llama-server (LLM Inference)', status: 'healthy', port: 11434, uptime: 14400 },
        { id: 'open-webui', name: 'Open WebUI (Chat)', status: 'healthy', port: 3000, uptime: 14400, public_url: 'https://chat.example.test' },
        { id: 'hermes-proxy', name: 'Hermes Auth Proxy', status: 'healthy', port: 9120, uptime: 14400, public_url: 'https://hermes.example.test' },
      ],
    }

    await renderDashboard(statusWithLaunchTargets)

    expect(await screen.findByRole('link', { name: /AI Chat/ })).toHaveAttribute('href', 'https://chat.example.test')
    expect(screen.getByRole('link', { name: /Hermes Agent/ })).toHaveAttribute('href', 'https://hermes.example.test')
    expect(screen.getByRole('link', { name: /Hermes Single Sign-On/ })).toHaveAttribute('href', '/invites')
    expect(screen.queryByRole('link', { name: /Remote Access/ })).not.toBeInTheDocument()
    expect(screen.getByText('Remote Access')).toBeInTheDocument()
  })

  it('keeps legacy feature cards away from raw backend API ports', async () => {
    mockFeatures = [
      {
        id: 'chat',
        name: 'AI Chat',
        description: 'Chat with a local model',
        icon: 'MessageSquare',
        status: 'enabled',
        requirements: { servicesAny: ['llama-server'], servicesMissing: [] },
      },
      {
        id: 'hermes-agent',
        name: 'Hermes Agent',
        description: 'Advanced Hermes agent console',
        icon: 'MessageSquare',
        status: 'enabled',
        requirements: { servicesAll: ['llama-server'], servicesMissing: [] },
      },
      {
        id: 'hermes-sso',
        name: 'Hermes Single Sign-On',
        description: 'Manage Hermes access',
        icon: 'MessageSquare',
        status: 'enabled',
        requirements: { servicesAll: ['hermes', 'dashboard-api'], servicesMissing: [] },
      },
      {
        id: 'usage-api',
        name: 'Usage API',
        description: 'Internal usage telemetry backend',
        icon: 'MessageSquare',
        status: 'enabled',
        requirements: { servicesAll: ['token-spy'], servicesMissing: [] },
      },
    ]

    const statusWithRawBackends = {
      ...baseStatus,
      services: [
        { id: 'llama-server', name: 'llama-server (LLM Inference)', status: 'healthy', port: 11434, uptime: 14400 },
        { id: 'litellm', name: 'LiteLLM (API Gateway)', status: 'healthy', port: 4000, uptime: 14400 },
        { id: 'token-spy', name: 'Token Spy (Usage Monitor)', status: 'healthy', port: 3005, uptime: 14400 },
        { id: 'open-webui', name: 'Open WebUI (Chat)', status: 'healthy', port: 3000, uptime: 14400 },
        { id: 'hermes-proxy', name: 'Hermes Auth Proxy', status: 'healthy', port: 9120, uptime: 14400 },
      ],
    }

    await renderDashboard(statusWithRawBackends)

    expect(await screen.findByRole('link', { name: /AI Chat/ })).toHaveAttribute('href', 'http://localhost:3000')
    expect(screen.getByRole('link', { name: /Hermes Agent/ })).toHaveAttribute('href', 'http://localhost:9120')
    expect(screen.getByRole('link', { name: /Hermes Single Sign-On/ })).toHaveAttribute('href', '/invites')
    expect(screen.queryByRole('link', { name: /Usage API/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /AI Chat/ })).not.toHaveAttribute('href', 'http://localhost:11434')
    expect(screen.queryByRole('link', { name: /Hermes Agent/ })).not.toHaveAttribute('href', 'http://localhost:11434')
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

  it('shows measured auxiliary containers without granting service restart actions', async () => {
    mockResources = {
      services: [{
        id: 'librechat-mongodb', name: 'librechat-mongodb', type: 'docker',
        restartable: false,
        restart_unavailable_reason: 'Service is not declared in the active manifest set',
        container: { container_name: 'ods-librechat-mongodb', cpu_percent: 4, memory_used_mb: 256 },
        disk: null,
      }],
    }
    await renderDashboard({ ...baseStatus, services: [] })
    const row = await screen.findByTestId('service-row-librechat-mongodb')
    expect(within(row).getByText('4.0%')).toBeInTheDocument()
    expect(within(row).getByText('256 MB')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /actions/ })).not.toBeInTheDocument()
    expect(within(row).getByTitle('Service is not declared in the active manifest set')).toBeInTheDocument()
    expect(restartCalls).toHaveLength(0)
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
    expect(restartingPill.className).toContain('text-theme-text-secondary')
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
    localStorage.setItem('ods-system-overview-history-v2', JSON.stringify([
      { t: now - 300000, tokensPerSecond: 20, model:'qwen', totalTokens: 4000 },
      { t: now - 60000, tokensPerSecond: 10, model:'qwen', totalTokens: 4500 },
    ]))

    await renderDashboard({ ...baseStatus, inference: { ...baseStatus.inference, tokensPerSecond: 10 } })

    const delta = screen.getByText('↓ 50.0%')
    expect(delta).toBeInTheDocument()
    expect(delta.className).toContain('text-red-400')
  })
})
