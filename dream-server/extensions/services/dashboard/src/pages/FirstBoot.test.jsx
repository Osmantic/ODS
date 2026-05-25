import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import FirstBoot from './FirstBoot' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const ownerCardReady = { ready: true, requires: 'dream-proxy', reason: '' }

async function finishWizard() {
  fireEvent.change(screen.getByDisplayValue('dream'), { target: { value: 'spark' } })
  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

  fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'sam' } })
  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
  const finishButton = screen.getByRole('button', { name: /^finish$/i })
  await waitFor(() => expect(finishButton).toBeEnabled())
  fireEvent.click(finishButton)
}

describe('FirstBoot', () => {
  beforeEach(() => {
    globalThis.localStorage.removeItem('dream-firstboot-progress')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('dream-firstboot-progress')
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
          requires: 'dream-proxy',
          reason: 'Dream Talk owner cards require dream-proxy.',
        })
      }
      if (url === '/api/setup/complete') {
        return response({ success: true })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={onComplete} />)

    fireEvent.change(screen.getByDisplayValue('dream'), { target: { value: 'spark' } })
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'sam' } })
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

    expect(await screen.findByText(/owner cards require dream-proxy/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }))

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith('/api/setup/complete', { method: 'POST' })
    expect(fetchMock).not.toHaveBeenCalledWith('/api/auth/magic-link/generate', expect.anything())
  })
})
