import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Invites from './Invites' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

test('offers a retry when QR generation fails', async () => {
  let qrAttempts = 0
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/auth/magic-link/list') return response({ tokens: [] })
    if (url === '/api/auth/magic-link/owner-card/status') return response({ ready: true, url_mode: 'lan' })
    if (url === '/api/auth/magic-link/generate') return response({ url: 'http://auth.test/owner-link', target_username: 'sam', token_type: 'owner', scope: 'hermes' })
    if (String(url).startsWith('/api/auth/magic-link/qr?url=')) {
      qrAttempts += 1
      return qrAttempts === 1 ? response({ detail: 'temporary QR failure' }, 502) : response({ data_url: 'data:image/png;base64,recovered' })
    }
    throw new Error(`unexpected request: ${url}`)
  }))

  render(<Invites />)
  await screen.findByText('No owner cards yet')
  fireEvent.click(screen.getByRole('button', { name: 'Print owner card' }))
  fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'sam' } })
  fireEvent.click(screen.getByRole('button', { name: 'Generate owner QR' }))
  await screen.findByRole('dialog', { name: 'Owner card created' })
  expect(await screen.findByRole('button', { name: 'Retry QR generation' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry QR generation' }))
  await waitFor(() => expect(screen.getByAltText('QR code for owner card')).toHaveAttribute('src', 'data:image/png;base64,recovered'))
})
