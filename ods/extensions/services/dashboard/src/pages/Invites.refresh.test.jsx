import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Invites from './Invites' // eslint-disable-line no-unused-vars

const response = body => ({ ok: true, status: 200, json: async () => body })

test('an older refresh cannot restore a card after successful revocation', async () => {
  const card = {
    token_hash_prefix: 'abc12345', target_username: 'owner', token_type: 'owner',
    scope: 'hermes', reusable: true, expires_at: null, redemption_count: 0,
    revoked_at: null, created_at: '2026-09-01T00:00:00Z',
  }
  let finishOld
  const oldRequest = new Promise(resolve => { finishOld = resolve })
  let lists = 0
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (url === '/api/auth/magic-link/list') {
      lists += 1
      if (lists === 2) return oldRequest
      return response({ tokens: lists === 1 ? [card] : [] })
    }
    if (url === '/api/auth/magic-link/owner-card/status') return response({ ready: true })
    if (options.method === 'DELETE') return response({ revoked: true })
    if (url === '/api/talk/status') return response({})
    throw new Error(`Unexpected request: ${url}`)
  }))
  render(<Invites />)
  await screen.findByRole('button', { name: /revoke owner card for owner/i })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh setup owner links' }))
  await waitFor(() => expect(lists).toBe(2))
  fireEvent.click(screen.getByRole('button', { name: /revoke owner card for owner/i }))
  await screen.findByText('No owner cards yet')
  await act(async () => { finishOld(response({ tokens: [card] })) })
  expect(screen.getByText('No owner cards yet')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /revoke owner card for owner/i })).not.toBeInTheDocument()
})
