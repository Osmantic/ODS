import { fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import PrivacyShieldCard from './PrivacyShieldCard' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const activeStatus = {
  enabled: true,
  container_running: true,
  port: 8085,
  target_api: 'http://llama-server:8080/v1',
  pii_cache_enabled: true,
  message: 'Privacy Shield is active',
}

const stats = {
  cache_enabled: true,
  cache_size: 512,
  active_sessions: 3,
  total_pii_scrubbed: 47,
}

const renderCard = (override = null, props = {}) => {
  const fetchMock = vi.fn(async (url, options) => {
    if (override) {
      const overridden = override(url, options)
      if (overridden) return overridden
    }
    if (url === '/api/privacy-shield/status') return response(activeStatus)
    if (url === '/api/privacy-shield/stats') return response(stats)
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { ...render(<PrivacyShieldCard deployed {...props} />), fetchMock }
}

describe('PrivacyShieldCard', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders nothing and makes no request when the service is not deployed', () => {
    const { fetchMock } = renderCard(null, { deployed: false })

    expect(screen.queryByTestId('privacy-shield-card')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('shows the shield state and its counters', async () => {
    renderCard()

    expect(await screen.findByText('Privacy Shield is active')).toBeInTheDocument()
    expect(screen.getByText('47')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('On (512)')).toBeInTheDocument()
    expect(screen.getByText('8085')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })

  test('stopping the shield posts enable:false and re-reads the status', async () => {
    const { fetchMock } = renderCard((url) => (
      url === '/api/privacy-shield/toggle'
        ? response({ success: true, message: 'Privacy Shield stopped.' })
        : null
    ))
    await screen.findByRole('button', { name: 'Stop' })

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))

    expect(await screen.findByText('Privacy Shield stopped.')).toBeInTheDocument()
    const toggleCall = fetchMock.mock.calls.find(([url]) => url === '/api/privacy-shield/toggle')
    expect(toggleCall[1].method).toBe('POST')
    expect(JSON.parse(toggleCall[1].body)).toEqual({ enable: false })
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/privacy-shield/status')).toHaveLength(2)
  })

  test('offers to start the shield when it is down', async () => {
    renderCard((url) => (
      url === '/api/privacy-shield/status'
        ? response({ ...activeStatus, enabled: false, container_running: false, message: 'Privacy Shield is not running.' })
        : null
    ))

    expect(await screen.findByRole('button', { name: 'Start' })).toBeInTheDocument()
  })

  test('reports a failed toggle even though the endpoint answers 200', async () => {
    renderCard((url) => (
      url === '/api/privacy-shield/toggle'
        ? response({ success: false, message: 'Host agent not reachable' })
        : null
    ))
    await screen.findByRole('button', { name: 'Stop' })

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))

    expect(await screen.findByText('Host agent not reachable')).toBeInTheDocument()
  })

  test('keeps the card usable when stats are unavailable', async () => {
    renderCard((url) => (
      url === '/api/privacy-shield/stats'
        ? response({ error: 'SHIELD_API_KEY not configured', enabled: false })
        : null
    ))

    expect(await screen.findByText('Privacy Shield is active')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })

  test('surfaces a status endpoint failure', async () => {
    renderCard((url) => (
      url === '/api/privacy-shield/status' ? response({}, 503) : null
    ))

    expect(await screen.findByText('Privacy Shield status unavailable (503)')).toBeInTheDocument()
  })
})
