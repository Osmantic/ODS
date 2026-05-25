import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Invites from './Invites' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const future = new Date(Date.now() + 3_600_000).toISOString()
const ownerCardReady = { ready: true, requires: 'dream-proxy', reason: '' }

describe('Invites', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders Setup / Owner first and revokes active owner cards', async () => {
    let listCount = 0
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/list') {
        listCount += 1
        return response({
          tokens: listCount === 1 ? [{
            token_hash_prefix: 'abc12345',
            target_username: 'owner',
            scope: 'hermes',
            reusable: true,
            token_type: 'owner',
            url_mode: 'lan',
            created_at: new Date().toISOString(),
            expires_at: null,
            redemption_count: 0,
            last_redeemed_at: null,
            revoked_at: null,
            note: 'factory card',
          }] : [],
        })
      }
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/auth/magic-link/abc12345' && options.method === 'DELETE') {
        return response({ revoked: true })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Invites />)

    expect(await screen.findByRole('heading', { name: 'Setup / Owner' })).toBeInTheDocument()
    expect(screen.getByText('Factory owner card')).toBeInTheDocument()
    expect(screen.getAllByText('owner').length).toBeGreaterThan(0)
    expect(screen.getByText('revoke-only')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /revoke owner card for owner/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/auth/magic-link/abc12345',
        expect.objectContaining({ method: 'DELETE' }),
      )
    })
    expect(await screen.findByText('No owner cards yet')).toBeInTheDocument()
  })

  test('generates owner card with revoke-only Dream Talk payload and loads QR', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/list') {
        return response({ tokens: [] })
      }
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/auth/magic-link/generate' && options.method === 'POST') {
        return response({
          token: 'plain-owner-token',
          url: 'http://auth.dream.local/magic-link/plain-owner-token',
          expires_at: null,
          target_username: 'mike',
          scope: 'hermes',
          reusable: true,
          token_type: 'owner',
          url_mode: 'lan',
        })
      }
      if (String(url).startsWith('/api/auth/magic-link/qr?url=')) {
        return response({ data_url: 'data:image/png;base64,ownerqr' })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Invites />)

    await screen.findByText('No owner cards yet')
    fireEvent.click(screen.getByRole('button', { name: 'Create owner card' }))
    fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'mike' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate owner QR' }))

    await screen.findByRole('dialog', { name: 'Owner card created' })
    const generateCall = fetchMock.mock.calls.find(([url]) => url === '/api/auth/magic-link/generate')
    const body = JSON.parse(generateCall[1].body)
    expect(body).toMatchObject({
      target_username: 'mike',
      token_type: 'owner',
      scope: 'hermes',
      url_mode: 'lan',
    })
    expect(body).not.toHaveProperty('expires_in')
    expect(screen.getByDisplayValue('http://auth.dream.local/magic-link/plain-owner-token')).toBeInTheDocument()
    expect(await screen.findByAltText('QR code for owner card')).toHaveAttribute('src', 'data:image/png;base64,ownerqr')
  })

  test('generates guest invite from the backend URL and loads QR', async () => {
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/list') {
        return response({ tokens: [] })
      }
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response(ownerCardReady)
      }
      if (url === '/api/auth/magic-link/generate' && options.method === 'POST') {
        return response({
          token: 'plain-secret-token',
          url: 'http://auth.dream.local/magic-link/plain-secret-token',
          expires_at: future,
          target_username: 'bob',
          scope: 'chat',
          reusable: false,
          token_type: 'guest',
          url_mode: 'auto',
        })
      }
      if (String(url).startsWith('/api/auth/magic-link/qr?url=')) {
        return response({ data_url: 'data:image/png;base64,abc123' })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Invites />)

    await screen.findByText('No guest invites yet')
    fireEvent.click(screen.getByRole('button', { name: 'Create guest invite' }))
    fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'bob' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))

    await screen.findByRole('dialog', { name: 'Invite created' })
    const generateCall = fetchMock.mock.calls.find(([url]) => url === '/api/auth/magic-link/generate')
    expect(JSON.parse(generateCall[1].body)).toMatchObject({
      target_username: 'bob',
      token_type: 'guest',
      scope: 'chat',
      reusable: false,
    })
    expect(screen.getByDisplayValue('http://auth.dream.local/magic-link/plain-secret-token')).toBeInTheDocument()
    expect(await screen.findByAltText('QR code for invite link')).toHaveAttribute('src', 'data:image/png;base64,abc123')
  })

  test('shows voice fallback when the browser origin is not secure', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, 'isSecureContext')
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/auth/magic-link/list') return response({ tokens: [] })
      if (url === '/api/auth/magic-link/owner-card/status') return response(ownerCardReady)
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Invites />)

    expect(await screen.findByText('Voice readiness')).toBeInTheDocument()
    expect(screen.getByText(/Mobile browsers usually block live microphone access/i)).toBeInTheDocument()

    if (descriptor) Object.defineProperty(window, 'isSecureContext', descriptor)
  })

  test('warns and disables owner card creation when dream-proxy is unavailable', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/auth/magic-link/list') return response({ tokens: [] })
      if (url === '/api/auth/magic-link/owner-card/status') {
        return response({
          ready: false,
          requires: 'dream-proxy',
          reason: 'Dream Talk owner cards require dream-proxy.',
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Invites />)

    expect(await screen.findByText(/Dream Talk owner cards require dream-proxy/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Print owner card' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Create owner card' })).toBeDisabled()
  })
})
