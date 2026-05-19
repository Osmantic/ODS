import { fireEvent, screen, within } from '@testing-library/react'
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

function installFetchMock({ current = currentReport, previous = previousReport } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const value = String(url).includes('start=2026-04-01') ? previous : current
    return {
      ok: true,
      json: async () => value,
    }
  }))
}

describe('Usage page', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-05-16T12:00:00Z'))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('renders the full usage dashboard from real Token Spy data', async () => {
    installFetchMock()
    render(<Usage status={{ tier: 'Minimal', version: '2.0.0', inference: { loadedModel: 'qwen3.5-9b' } }} />)

    expect(await screen.findByRole('heading', { name: 'Usage' })).toBeInTheDocument()
    expect(screen.getByText('Cost Estimate')).toBeInTheDocument()
    expect(screen.getByText('Tokens')).toBeInTheDocument()
    expect(screen.getAllByText('Requests')[0]).toBeInTheDocument()
    expect(screen.getByText('Tracked Providers')).toBeInTheDocument()
    expect(screen.getByText('Daily Cost Estimate')).toBeInTheDocument()
    expect(screen.getByText('Tokens per Day')).toBeInTheDocument()
    expect(screen.getByText('Cost Confidence')).toBeInTheDocument()
    expect(screen.getByText('Tracking Source Guide')).toBeInTheDocument()
    expect(screen.getByText('Usage by Model')).toBeInTheDocument()
    expect(screen.getByText('Top Consumers by Tokens')).toBeInTheDocument()
    expect(screen.getByText('Tokens by Service')).toBeInTheDocument()
    expect(screen.getAllByText('$3.75')[0]).toBeInTheDocument()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getAllByText('qwen3.5-9b')[0]).toBeInTheDocument()
  })

  it('keeps the layout honest when Token Spy has no data', async () => {
    installFetchMock({ current: makeEmptyReport(), previous: makeEmptyReport('2026-04-01', '2026-04-30') })
    render(<Usage status={{}} />)

    expect(await screen.findByText('Token Spy unavailable')).toBeInTheDocument()
    expect(screen.getAllByText('$0.00')[0]).toBeInTheDocument()
    expect(screen.getAllByText('No tracked usage for this period')[0]).toBeInTheDocument()
    expect(screen.queryByText('$77.67')).not.toBeInTheDocument()
    expect(screen.queryByText('359,573,723')).not.toBeInTheDocument()
  })

  it('filters the model table locally by query, provider, service, and source', async () => {
    installFetchMock()
    render(<Usage status={{}} />)
    await screen.findByText('gpt-4o')

    fireEvent.change(screen.getByPlaceholderText('Search models...'), { target: { value: 'qwen' } })
    expect(screen.getByText('qwen3.5-9b')).toBeInTheDocument()
    expect(screen.queryByText('gpt-4o')).not.toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Search models...'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('All Providers'), { target: { value: 'local' } })
    expect(screen.getByText('qwen3.5-9b')).toBeInTheDocument()
    expect(screen.queryByText('gpt-4o')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('All Providers'), { target: { value: 'all' } })
    fireEvent.change(screen.getByLabelText('All Services'), { target: { value: 'Perplexica' } })
    expect(screen.getByText('unknown-model')).toBeInTheDocument()
    expect(screen.queryByText('qwen3.5-9b')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('All Services'), { target: { value: 'all' } })
    fireEvent.change(screen.getByLabelText('All Sources'), { target: { value: 'priced_from_tokens' } })
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.queryByText('unknown-model')).not.toBeInTheDocument()
  })

  it('switches estimate chart modes and exports the filtered real rows', async () => {
    installFetchMock()
    const clickSpy = vi.spyOn(globalThis.HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:usage-csv'),
      revokeObjectURL: vi.fn(),
    })

    render(<Usage status={{}} />)
    await screen.findByText('gpt-4o')

    fireEvent.click(screen.getByRole('button', { name: 'weekly' }))
    expect(screen.getByRole('button', { name: 'weekly' })).toHaveClass('bg-theme-accent/25')

    fireEvent.change(screen.getByPlaceholderText('Search models...'), { target: { value: 'gpt-4o' } })
    fireEvent.click(screen.getByRole('button', { name: /Export CSV/i }))

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })

  it('renders untracked model rows with unknown cost rather than zeroing them as paid', async () => {
    installFetchMock()
    render(<Usage status={{}} />)
    await screen.findByText('unknown-model')

    const row = screen.getByText('unknown-model').closest('div.grid')
    expect(within(row).getByText('untracked')).toBeInTheDocument()
    expect(within(row).getByText('-')).toBeInTheDocument()
  })
})
