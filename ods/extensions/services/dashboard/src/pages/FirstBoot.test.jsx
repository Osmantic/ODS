import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import FirstBoot from './FirstBoot' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const ownerCardReady = { ready: true, requires: 'ods-proxy', reason: '' }

async function finishWizard(stackName = null) {
  fireEvent.change(screen.getByDisplayValue('ods'), { target: { value: 'spark' } })
  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

  fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'sam' } })
  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

  if (stackName) {
    fireEvent.click(screen.getByRole('button', { name: new RegExp(stackName, 'i') }))
  }
  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
  const finishButton = screen.getByRole('button', { name: /^finish$/i })
  await waitFor(() => expect(finishButton).toBeEnabled())
  fireEvent.click(finishButton)
}

describe('FirstBoot', () => {
  beforeEach(() => {
    globalThis.localStorage.removeItem('ods-firstboot-progress')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-firstboot-progress')
  })

  test('generates the owner card, marks setup complete, and shows the QR', async () => {
    const onComplete = vi.fn()
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/auth/magic-link/generate' && options.method === 'POST') {
        return response({
          url: 'http://auth.spark.local/magic-link/first-token',
          target_username: 'sam',
          expires_at: null,
          scope: 'hermes',
          reusable: true,
          token_type: 'owner',
          url_mode: 'lan',
        })
      }
      if (url === '/api/setup/complete' && options.method === 'POST') {
        return response({ success: true })
      }
      if (String(url).startsWith('/api/auth/magic-link/qr?url=')) {
        return response({ data_url: 'data:image/png;base64,qrpayload' })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={onComplete} />)

    await finishWizard()

    expect(await screen.findByRole('heading', { name: /you're set/i })).toBeInTheDocument()
    const generateCall = fetchMock.mock.calls.find(([url]) => url === '/api/auth/magic-link/generate')
    expect(JSON.parse(generateCall[1].body)).toMatchObject({
      target_username: 'sam',
      token_type: 'owner',
      scope: 'hermes',
      url_mode: 'lan',
      note: 'First-boot owner card (spark)',
    })
    expect(JSON.parse(generateCall[1].body)).not.toHaveProperty('expires_in')
    expect(fetchMock).toHaveBeenCalledWith('/api/setup/complete', { method: 'POST' })
    expect(await screen.findByAltText('QR code for owner card')).toHaveAttribute('src', 'data:image/png;base64,qrpayload')

    fireEvent.click(screen.getByRole('button', { name: /open dashboard/i }))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  test('applies the selected agent stack before creating credentials or completing setup', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/templates/onboarding-agents/apply' && options.method === 'POST') {
        return response({
          enabled_count: 4,
          started_count: 4,
          failed_services: [],
          skipped_services: [],
          warnings: [],
          restart_required: false,
        })
      }
      if (url === '/api/auth/magic-link/generate' && options.method === 'POST') {
        return response({
          url: 'http://auth.spark.local/magic-link/agent-token',
          target_username: 'sam',
        })
      }
      if (url === '/api/setup/complete' && options.method === 'POST') {
        return response({ success: true })
      }
      if (String(url).startsWith('/api/auth/magic-link/qr?url=')) {
        return response({ data_url: 'data:image/png;base64,qrpayload' })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    await finishWizard('Chat \\+ Agents')

    expect(await screen.findByRole('heading', { name: /you're set/i })).toBeInTheDocument()
    const urls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(urls.indexOf('/api/templates/onboarding-agents/apply')).toBeLessThan(
      urls.indexOf('/api/auth/magic-link/generate'),
    )
    expect(urls.indexOf('/api/auth/magic-link/generate')).toBeLessThan(
      urls.indexOf('/api/setup/complete'),
    )
  })

  test('keeps first-run active when a selected stack is only partially applied', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/templates/onboarding-full-stack/apply' && options.method === 'POST') {
        return response({
          enabled_count: 8,
          started_count: 8,
          failed_services: [],
          skipped_services: ['comfyui'],
          warnings: ['comfyui requires a compatible GPU'],
          restart_required: false,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    await finishWizard('Full ODS Stack')

    expect(await screen.findByText(/only partially configured/i)).toBeInTheDocument()
    expect(screen.getByText(/not compatible or unavailable: comfyui/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/setup/complete', expect.anything())
    expect(fetchMock).not.toHaveBeenCalledWith('/api/auth/magic-link/generate', expect.anything())
    expect(screen.queryByRole('heading', { name: /you're set/i })).not.toBeInTheDocument()
  })

  test('keeps first-run active when apply counts report an unlisted start failure', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/templates/onboarding-agents/apply' && options.method === 'POST') {
        return response({
          enabled_count: 4,
          started_count: 3,
          failed_services: [],
          skipped_services: [],
          warnings: [],
          restart_required: true,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    await finishWizard('Chat \\+ Agents')

    expect(await screen.findByText(/started 3 of 4 enabled services/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/setup/complete', expect.anything())
    expect(fetchMock).not.toHaveBeenCalledWith('/api/auth/magic-link/generate', expect.anything())
  })

  test('rejects malformed template apply failure lists', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/templates/onboarding-agents/apply' && options.method === 'POST') {
        return response({
          enabled_count: 4,
          started_count: 4,
          failed_services: 'none',
          skipped_services: [],
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    await finishWizard('Chat \\+ Agents')

    expect(await screen.findByText(/invalid apply result/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/setup/complete', expect.anything())
  })

  test('rejects an incomplete template apply receipt', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/templates/onboarding-agents/apply' && options.method === 'POST') {
        return response({
          failed_services: [],
          skipped_services: [],
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    await finishWizard('Chat \\+ Agents')

    expect(await screen.findByText(/invalid apply result/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/setup/complete', expect.anything())
    expect(fetchMock).not.toHaveBeenCalledWith('/api/auth/magic-link/generate', expect.anything())
  })

  test('rejects impossible template apply counts', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/templates/onboarding-agents/apply' && options.method === 'POST') {
        return response({
          enabled_count: 3,
          started_count: 4,
          failed_services: [],
          skipped_services: [],
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    await finishWizard('Chat \\+ Agents')

    expect(await screen.findByText(/invalid apply result/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/setup/complete', expect.anything())
    expect(fetchMock).not.toHaveBeenCalledWith('/api/auth/magic-link/generate', expect.anything())
  })

  test('does not show success when setup completion fails', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/auth/magic-link/generate' && options.method === 'POST') {
        return response({
          url: 'http://auth.spark.local/magic-link/first-token',
          target_username: 'sam',
          expires_at: null,
          scope: 'hermes',
          reusable: true,
          token_type: 'owner',
          url_mode: 'lan',
        })
      }
      if (url === '/api/setup/complete' && options.method === 'POST') {
        return response({ detail: 'sentinel write failed' }, 500)
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)

    await finishWizard()

    await waitFor(() => expect(screen.getByText(/sentinel write failed/i)).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: /you're set/i })).not.toBeInTheDocument()
  })

  test('finishes setup without an owner card when LAN access is unavailable', async () => {
    const onComplete = vi.fn()
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response({
          ready: false,
          requires: 'ods-proxy',
          reason: 'ODS Talk owner cards require ods-proxy.',
        })
      }
      if (url === '/api/setup/complete') {
        return response({ success: true })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={onComplete} />)

    fireEvent.change(screen.getByDisplayValue('ods'), { target: { value: 'spark' } })
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'sam' } })
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

    expect(await screen.findByText(/owner cards require ods-proxy/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }))

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith('/api/setup/complete', { method: 'POST' })
    expect(fetchMock).not.toHaveBeenCalledWith('/api/auth/magic-link/generate', expect.anything())
  })
})
