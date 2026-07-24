import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Settings from './Settings' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const summary = {
  version: '2.5.3',
  install_date: '2026-07-20T14:58:20Z',
  tier: 'entry',
  uptime: 3600,
  services: [
    { id: 'dashboard', name: 'Dashboard', status: 'healthy', port: 3001 },
  ],
}

const storage = {
  models: { formatted: '8.0 GB', gb: 8, percent: 1.6 },
  vector_db: { formatted: '2.0 GB', gb: 2, percent: 0.4 },
  total_data: { formatted: '12.0 GB', gb: 12, percent: 2.4 },
  disk: { used_gb: 62.5, total_gb: 500, percent: 12.5 },
}

const editor = {
  path: '.env',
  fields: {
    ODS_VERSION: {
      key: 'ODS_VERSION',
      label: 'ODS Version',
      description: 'ODS version for update compatibility checks.',
      type: 'string',
      secret: false,
      required: false,
      readOnly: true,
      default: null,
    },
    HOST_LAN_IP: {
      key: 'HOST_LAN_IP',
      label: 'LAN Host IP',
      description: 'Host address exposed to services.',
      type: 'string',
      secret: false,
      required: false,
      readOnly: false,
      default: null,
    },
  },
  sections: [{ id: 'configuration', title: 'Configuration', keys: ['ODS_VERSION', 'HOST_LAN_IP'] }],
  values: { ODS_VERSION: '2.5.3', HOST_LAN_IP: '192.168.1.10' },
  issues: [],
  applyPlan: null,
  agentAvailable: true,
}

const payloadByUrl = (url) => {
  if (url === '/api/settings/summary') return summary
  if (url === '/api/storage') return storage
  if (url === '/api/settings/env') return editor
  if (String(url).startsWith('/api/usage/report?')) {
    return {
      summary: { total_tokens: 16400, requests: 42 },
      models: [{ model: 'qwen' }, { model: 'phi' }],
      source: { status: 'ok' },
    }
  }
  if (url === '/api/setup/status') return { first_run: false, persona: null }
  if (url === '/api/version') {
    return { current: '2.5.3', latest: '2.5.3', update_available: false, checked_at: '2026-07-23T12:00:00Z' }
  }
  throw new Error(`Unexpected request: ${url}`)
}

const renderSettings = (override = null) => {
  const fetchMock = vi.fn(async (url) => {
    if (override) {
      const overridden = override(url)
      if (overridden) return overridden
    }
    return response(payloadByUrl(url))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { ...render(<Settings />), fetchMock }
}

describe('Settings', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-theme')
  })

  test('renders storage capacity and an ODS data breakdown from the API contract', async () => {
    renderSettings()

    expect(await screen.findByRole('heading', { name: 'Storage' })).toBeInTheDocument()
    expect(screen.getByText('12.0 GB')).toBeInTheDocument()
    expect(screen.getByText('62.5 GB of 500.0 GB')).toBeInTheDocument()
    expect(screen.getByText('./data')).toBeInTheDocument()
    expect(screen.getByText('Models')).toBeInTheDocument()
    expect(screen.getByText('Vector DB')).toBeInTheDocument()
    expect(screen.getByText('Service data')).toBeInTheDocument()
    expect(screen.queryByText('Other ODS data')).not.toBeInTheDocument()
    expect(screen.getByText('Includes bind-mounted ODS service data. Docker image layers are managed separately by Docker.')).toBeInTheDocument()
    expect(screen.getByLabelText('Host disk 12.5% used')).toBeInTheDocument()
    const accountCard = screen.getByRole('link', { name: /Account Usage/ })
    expect(within(accountCard).getByText('16.4k')).toBeInTheDocument()
    expect(within(accountCard).getByText('42')).toBeInTheDocument()
    expect(within(accountCard).getByText('2')).toBeInTheDocument()
  })

  test('clamps malformed storage values without rendering invalid widths', async () => {
    const { container } = renderSettings((url) => (
      url === '/api/storage'
        ? response({
            models: { gb: -3 },
            vector_db: { gb: 'invalid' },
            total_data: { gb: 4 },
            disk: { used_gb: -8, total_gb: 0, percent: 250 },
          })
        : null
    ))

    expect(await screen.findByText('Disk capacity unavailable')).toBeInTheDocument()
    expect(screen.getByLabelText('Host disk 100% used').firstElementChild).toHaveStyle({ width: '100%' })
    expect(container.innerHTML).not.toContain('NaN')
    expect(container.innerHTML).not.toContain('Infinity')
  })

  test('counts unique model identities instead of per-service report rows', async () => {
    renderSettings((url) => (
      String(url).startsWith('/api/usage/report?')
        ? response({
            summary: { total_tokens: 100, requests: 3 },
            models: [
              { model: 'qwen', service: 'litellm' },
              { model: 'qwen', service: 'model-router' },
              { model: 'phi', service: 'hermes' },
            ],
            source: { status: 'ok' },
          })
        : null
    ))

    const accountCard = await screen.findByRole('link', { name: /Account Usage/ })
    const modelMetric = within(accountCard).getByText('Models Used').parentElement
    expect(within(modelMetric).getByText('2')).toBeInTheDocument()
  })

  test('keeps successfully loaded settings visible when storage is unavailable', async () => {
    renderSettings((url) => (
      url === '/api/storage'
        ? response({ detail: 'storage probe failed' }, 503)
        : null
    ))

    expect(await screen.findByRole('heading', { name: 'System Identity' })).toBeInTheDocument()
    expect(screen.getByText('Some settings details are temporarily unavailable. Showing the data that loaded successfully.')).toBeInTheDocument()
    expect(screen.getByText('Disk capacity unavailable')).toBeInTheDocument()
    expect(screen.getByText('No data yet')).toBeInTheDocument()
    expect(screen.getAllByText('Empty')).toHaveLength(3)
  })

  test('does not force dark inline card backgrounds when the light theme is selected', async () => {
    const { container } = renderSettings()
    await screen.findByRole('heading', { name: 'System Identity' })

    fireEvent.click(screen.getByRole('button', { name: 'Light' }))

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'light'))
    const cards = [...container.querySelectorAll('.liquid-metal-frame')]
    expect(cards.length).toBeGreaterThan(0)
    expect(cards.every(card => !card.getAttribute('style')?.includes('rgba(18,18,25'))).toBe(true)
  })

  test('does not render controls that advertise unimplemented field help or search shortcuts', async () => {
    renderSettings()

    expect(await screen.findByRole('heading', { name: 'Environment Editor' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'More information about ODS Version' })).not.toBeInTheDocument()
    expect(screen.queryByText('K', { selector: 'span' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'localhost' })).not.toBeInTheDocument()
  })

  test('preserves unsaved environment changes during a global refresh', async () => {
    const { fetchMock } = renderSettings()
    const input = await screen.findByDisplayValue('192.168.1.10')
    fireEvent.change(input, { target: { value: '192.168.1.25' } })

    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])

    await screen.findByText('System details refreshed. Unsaved environment changes were preserved.')
    expect(screen.getByDisplayValue('192.168.1.25')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/settings/env')).toHaveLength(2)
  })
})
