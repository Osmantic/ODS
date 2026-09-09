import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Usage from './Usage' // eslint-disable-line no-unused-vars

const currentReport = {
  period: { start: '2026-05-01', end: '2026-05-31' },
  source: { name: 'token-spy', status: 'ok', detail: null },
  summary: {
    spend_usd: 3.75,
    requests: 42,
    input_tokens: 12000,
    output_tokens: 3400,
    cache_read_tokens: 800,
    cache_write_tokens: 200,
    total_tokens: 16400,
    tracked_providers: 3,
    billing_providers: 1,
    local_providers: 1,
    untracked_providers: 1,
    paid_cost_usd: 3.75,
    local_cost_usd: 0,
  },
  daily: [
    {
      date: '2026-05-01',
      spend_usd: 1.25,
      requests: 12,
      input_tokens: 4000,
      output_tokens: 1200,
      cache_read_tokens: 300,
      cache_write_tokens: 100,
    },
    {
      date: '2026-05-02',
      spend_usd: 2.5,
      requests: 30,
      input_tokens: 8000,
      output_tokens: 2200,
      cache_read_tokens: 500,
      cache_write_tokens: 100,
    },
  ],
  models: [
    {
      model: 'gpt-4o',
      provider: 'openai',
      service: 'Open WebUI',
      cost_source: 'priced_from_tokens',
      requests: 20,
      input_tokens: 8200,
      output_tokens: 2100,
      cache_read_tokens: 600,
      cache_write_tokens: 0,
      cost_usd: 3.75,
    },
    {
      model: 'qwen3.5-9b',
      provider: 'local',
      service: 'llama-server',
      cost_source: 'local_zero_cost',
      requests: 18,
      input_tokens: 3200,
      output_tokens: 900,
      cache_read_tokens: 200,
      cache_write_tokens: 200,
      cost_usd: 0,
    },
    {
      model: 'unknown-model',
      provider: 'unknown',
      service: 'Perplexica',
      cost_source: 'untracked',
      requests: 4,
      input_tokens: 600,
      output_tokens: 400,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0,
    },
  ],
  services: [
    {
      service: 'Open WebUI',
      requests: 20,
      input_tokens: 8200,
      output_tokens: 2100,
      cache_read_tokens: 600,
      cache_write_tokens: 0,
      cost_usd: 3.75,
    },
    {
      service: 'llama-server',
      requests: 18,
      input_tokens: 3200,
      output_tokens: 900,
      cache_read_tokens: 200,
      cache_write_tokens: 200,
      cost_usd: 0,
    },
  ],
  sources: [
    {
      source: 'priced_from_tokens',
      requests: 20,
      input_tokens: 8200,
      output_tokens: 2100,
      cache_read_tokens: 600,
      cache_write_tokens: 0,
      cost_usd: 3.75,
    },
    {
      source: 'local_zero_cost',
      requests: 18,
      input_tokens: 3200,
      output_tokens: 900,
      cache_read_tokens: 200,
      cache_write_tokens: 200,
      cost_usd: 0,
    },
  ],
}

const previousReport = {
  ...currentReport,
  period: { start: '2026-04-01', end: '2026-04-30' },
  summary: { ...currentReport.summary, spend_usd: 2.5, requests: 21, total_tokens: 8200 },
  daily: [],
  models: [],
  services: [],
  sources: [],
}

const readyReadiness = {
  service_id: 'token-spy',
  status: 'ready',
  available: true,
  configured: true,
  installed: true,
  enabled: true,
  healthy: true,
  service_status: 'healthy',
  message: 'Usage tracking is ready.',
  detail: null,
  actions: {
    restart: {
      method: 'POST',
      url: '/api/services/token-spy/restart',
      label: 'Restart Token Spy',
    },
  },
}

const disabledReadiness = {
  service_id: 'token-spy',
  status: 'disabled',
  available: false,
  configured: true,
  installed: true,
  enabled: false,
  healthy: false,
  service_status: 'unknown',
  message: 'Usage tracking is not enabled for this stack.',
  detail: 'Enable Token Spy to collect future token, request, and cost-source telemetry.',
  actions: {
    enable: {
      method: 'POST',
      url: '/api/extensions/token-spy/enable?auto_enable_deps=true',
      label: 'Enable Usage Tracking',
    },
  },
}

const offlineReadiness = {
  service_id: 'token-spy',
  status: 'offline',
  available: false,
  configured: true,
  installed: true,
  enabled: true,
  healthy: false,
  service_status: 'down',
  message: 'Usage tracking is enabled but not healthy.',
  detail: 'Token Spy service status is down. Start or restart it, then refresh this page.',
  actions: {
    restart: {
      method: 'POST',
      url: '/api/services/token-spy/restart',
      label: 'Restart Token Spy',
    },
  },
}

function makeEmptyReport(start = '2026-05-01', end = '2026-05-31') {
  return {
    period: { start, end },
    source: { name: 'token-spy', status: 'unavailable', detail: 'Token Spy unavailable' },
    summary: {
      spend_usd: 0,
      requests: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 0,
      tracked_providers: 0,
      billing_providers: 0,
      local_providers: 0,
      untracked_providers: 0,
      paid_cost_usd: 0,
      local_cost_usd: 0,
    },
    daily: [],
    models: [],
    services: [],
    sources: [],
  }
}

function installFetchMock({
  current = currentReport,
  previous = previousReport,
  readiness = readyReadiness,
} = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const text = String(url)
    if (options.method === 'POST') {
      return {
        ok: true,
        json: async () => ({ message: 'Action accepted' }),
      }
    }
    if (text.includes('/api/usage/readiness')) {
      return {
        ok: true,
        json: async () => readiness,
      }
    }
    const value = text.includes('start=2026-04-01') ? previous : current
    return {
      ok: true,
      json: async () => value,
    }
  }))
}

describe('Usage page', () => {
  beforeEach(() => {
    vi.useFakeTimers({toFake:['Date']})
    vi.setSystemTime(new Date('2026-05-16T12:00:00Z'))
  })
  afterEach(()=>{vi.restoreAllMocks();vi.useRealTimers();vi.unstubAllGlobals()})
  async function ready() { await screen.findByText('Recorded activity · refreshes every 10s') }
  function tab(name) { fireEvent.click(screen.getByRole('button',{name,exact:true})) }

  it('puts token activity first and separates the detailed views',async()=>{
    installFetchMock();render(<Usage compact/>);await ready()
    expect(screen.queryByRole('heading',{name:'Usage',exact:true})).not.toBeInTheDocument()
    expect(screen.getByText('Total tokens')).toBeVisible()
    expect(screen.getByText('16.4K')).toBeVisible()
    expect(screen.getByRole('region',{name:'Tokens per day'})).toBeVisible()
    expect(screen.queryByText('Cost Estimate')).not.toBeInTheDocument()
    tab('Models');expect(screen.getByText('gpt-4o')).toBeVisible()
    tab('Services');expect(screen.getByText('Tokens by Service')).toBeVisible()
    tab('Costs');expect(screen.getByText('Cost Estimate')).toBeVisible()
    expect(screen.getByText('$3.75')).toBeVisible()
    expect(screen.getByText('Tracking Source Guide')).toBeVisible()
  })
  it('shows unavailable rather than invented zero consumption',async()=>{
    installFetchMock({current:makeEmptyReport(),readiness:disabledReadiness})
    render(<Usage/>)
    expect(await screen.findByText(disabledReadiness.message)).toBeVisible()
    expect(screen.getByText('Usage data unavailable')).toBeVisible()
    expect(screen.queryByText('0.00')).not.toBeInTheDocument()
    expect(screen.getAllByText('No verified data for this period')).toHaveLength(2)
    tab('Models');expect(screen.getByText('No tracked usage for this period')).toBeVisible()
  })
  it('filters by query, provider, service and source',async()=>{
    installFetchMock();render(<Usage/>);await ready();tab('Models')
    fireEvent.change(screen.getByPlaceholderText('Search models...'),{target:{value:'qwen'}})
    expect(screen.getByText('qwen3.5-9b')).toBeVisible()
    expect(screen.queryByText('gpt-4o')).not.toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Search models...'),{target:{value:''}})
    fireEvent.click(screen.getByText('Filters'))
    fireEvent.change(screen.getByLabelText('All Providers'),{target:{value:'local'}})
    expect(screen.queryByText('gpt-4o')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('All Providers'),{target:{value:'all'}})
    fireEvent.change(screen.getByLabelText('All Services'),{target:{value:'Perplexica'}})
    expect(screen.getByText('unknown-model')).toBeVisible()
    expect(screen.queryByText('qwen3.5-9b')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('All Services'),{target:{value:'all'}})
    fireEvent.change(screen.getByLabelText('All Sources'),{target:{value:'priced_from_tokens'}})
    expect(screen.getByText('gpt-4o')).toBeVisible()
    expect(screen.queryByText('unknown-model')).not.toBeInTheDocument()
  })
  it('switches cost modes and exports only the filtered rows',async()=>{
    installFetchMock()
    const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
    vi.stubGlobal('URL',{createObjectURL:vi.fn(()=>'blob:test'),revokeObjectURL:vi.fn()})
    render(<Usage/>);await ready();tab('Costs');tab('weekly')
    expect(screen.getByRole('button',{name:'weekly'})).toHaveAttribute('aria-pressed','true')
    tab('Models');fireEvent.change(screen.getByPlaceholderText('Search models...'),{target:{value:'gpt-4o'}})
    tab('Export CSV');expect(URL.createObjectURL).toHaveBeenCalledTimes(1);expect(click).toHaveBeenCalledTimes(1)
  })
  it('shows unknown cost without treating it as zero',async()=>{
    installFetchMock();render(<Usage/>);await ready();tab('Models')
    fireEvent.click(screen.getByText('unknown-model'))
    const row=screen.getByText('unknown-model').closest('details')
    expect(within(row).getByText('Unknown cost')).toBeVisible()
    expect(within(row).getByText('—')).toBeVisible()
  })
  it('can enable tracking and restart unhealthy tracking',async()=>{
    installFetchMock({current:makeEmptyReport(),readiness:disabledReadiness})
    const mounted=render(<Usage/>)
    fireEvent.click(await screen.findByRole('button',{name:'Enable Usage Tracking'}))
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/extensions/token-spy/enable?auto_enable_deps=true',{method:'POST'}))
    expect(await screen.findByText('Action accepted')).toBeVisible()
    mounted.unmount()
    installFetchMock({current:makeEmptyReport(),readiness:offlineReadiness})
    render(<Usage/>)
    fireEvent.click(await screen.findByRole('button',{name:'Restart Token Spy'}))
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/services/token-spy/restart',{method:'POST'}))
  })
  it('includes cache writes and recalculates the visible series scale',async()=>{
    installFetchMock();render(<Usage/>);await ready();tab('Cache')
    const plot=screen.getByRole('region',{name:'Tokens per day'})
    expect(within(plot).getByRole('button',{name:'May 1: Cache 400'})).toBeVisible()
    expect(within(plot).getByRole('button',{name:'May 2: Cache 600'})).toBeVisible()
  })
  it('paginates model rows and resets pagination after a filter',async()=>{
    const models=Array.from({length:17},(_,index)=>({...currentReport.models[0],model:'model-'+index}))
    installFetchMock({current:{...currentReport,models}})
    render(<Usage/>);await ready();tab('Models')
    expect(screen.getByRole('button',{name:'Previous models page'})).toBeDisabled()
    tab('Next models page');expect(screen.getByText('2 / 3')).toBeVisible()
    fireEvent.change(screen.getByPlaceholderText('Search models...'),{target:{value:'model-16'}})
    expect(screen.getByText('1 / 1')).toBeVisible()
    expect(screen.getByRole('button',{name:'Next models page'})).toBeDisabled()
  })
  it('does not draw an empty dollar chart for local-only inference',async()=>{
    installFetchMock({current:{...currentReport,summary:{...currentReport.summary,spend_usd:0,billing_providers:0,untracked_providers:0,local_providers:1}}})
    render(<Usage/>);await ready();tab('Costs')
    expect(screen.getByText(/Local inference · no external API charges/)).toBeVisible()
    expect(screen.queryByRole('region',{name:'Recorded cost'})).not.toBeInTheDocument()
  })
})
