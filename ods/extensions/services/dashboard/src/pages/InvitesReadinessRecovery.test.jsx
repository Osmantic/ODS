import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Invites from './Invites' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
})
const owner = {
  token_hash_prefix: 'abc12345', target_username: 'owner-device',
  token_type: 'owner', scope: 'hermes', reusable: true, redemption_count: 0,
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

test.each(['network', 'invalid JSON', 'HTTP 503'])('keeps access inventory and revocation usable during a readiness %s failure', async failure => {
  let revoked = false, ready = false
  const fetcher = vi.fn(async (url, options = {}) => {
    if (url.endsWith('/list')) return response({ tokens: revoked ? [] : [owner] })
    if (url.endsWith('/status')) {
      if (ready) return response({ ready: true })
      if (failure === 'network') throw new TypeError('Failed to fetch')
      if (failure === 'invalid JSON') return { ok: true, status: 200, json: async () => { throw new SyntaxError('Invalid JSON') } }
      return response({}, 503)
    }
    if (url.endsWith('/abc12345') && options.method === 'DELETE') { revoked = true; return response({ revoked: true }) }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetcher)
  render(<Invites />)
  const revoke = await screen.findByRole('button', { name: 'Revoke owner card for owner-device' })
  expect(screen.getByRole('button', { name: 'Print owner card' })).toBeDisabled()
  expect(screen.getByText(/Owner-card status unavailable/)).toBeVisible()
  expect(screen.getByRole('button', { name: 'New guest invite' })).toBeEnabled()
  fireEvent.click(revoke)
  expect(await screen.findByText('No owner cards yet')).toBeVisible()
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(1)
  ready = true
  fireEvent.click(screen.getByRole('button', { name: 'Refresh setup owner links' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Print owner card' })).toBeEnabled())
  expect(screen.queryByText(/Owner-card status unavailable/)).toBeNull()
})

test('drops stale readiness after its next request times out while applying the refreshed list', async () => {
  vi.useFakeTimers()
  let requests = 0, readinessSignal
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url.endsWith('/list')) return response({ tokens: ++requests === 1 ? [] : [owner] })
    if (requests === 1) return response({ ready: true })
    readinessSignal = options.signal
    return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
  }))
  await act(async () => render(<Invites />))
  expect(screen.getByRole('button', { name: 'Print owner card' })).toBeEnabled()
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh setup owner links' })))
  await act(async () => vi.advanceTimersByTimeAsync(8000))
  expect(readinessSignal.aborted).toBe(true)
  expect(screen.getByRole('button', { name: 'Revoke owner card for owner-device' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Print owner card' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Refresh setup owner links' })).toBeEnabled()
})

test('still reports required inventory failures and retains the last known access rows', async () => {
  let failed = false
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url.endsWith('/status')) return response({ ready: true })
    return failed ? response({}, 503) : response({ tokens: [owner] })
  }))
  render(<Invites />)
  await screen.findByRole('button', { name: 'Revoke owner card for owner-device' })
  failed = true
  fireEvent.click(screen.getByRole('button', { name: 'Refresh setup owner links' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('list failed: 503')
  expect(screen.getByRole('button', { name: 'Revoke owner card for owner-device' })).toBeEnabled()
  expect(screen.queryByText('No owner cards yet')).toBeNull()
})

test('also replaces stale readiness when both requests fail', async () => {
  let failed = false
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (failed) throw new TypeError('Failed to fetch')
    return response(url.endsWith('/status') ? { ready: true } : { tokens: [owner] })
  }))
  render(<Invites />)
  await screen.findByRole('button', { name: 'Revoke owner card for owner-device' })
  failed = true
  fireEvent.click(screen.getByRole('button', { name: 'Refresh setup owner links' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Failed to fetch')
  expect(screen.getByRole('button', { name: 'Print owner card' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Revoke owner card for owner-device' })).toBeEnabled()
})
