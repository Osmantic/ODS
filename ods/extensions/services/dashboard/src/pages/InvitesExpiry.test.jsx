import { act, fireEvent, render, screen } from '@testing-library/react'
import Invites from './Invites' // eslint-disable-line no-unused-vars

const epoch = new Date('2026-09-15T00:00:00Z')
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

async function openInventory() {
  vi.useFakeTimers()
  vi.setSystemTime(epoch)
  const tokens = [
    { token_hash_prefix: 'guest', target_username: 'alice', token_type: 'guest',
      expires_at: new Date(+epoch + 5000).toISOString(), redemption_count: 0, scope: 'chat' },
    { token_hash_prefix: 'owner', target_username: 'owner', token_type: 'owner',
      expires_at: new Date(+epoch + 5000).toISOString(), redemption_count: 0, scope: 'hermes' },
  ]
  const fetcher = vi.fn(async url => ({ ok: true, status: 200,
    json: async () => url.endsWith('/list') ? { tokens } : { ready: true } }))
  vi.stubGlobal('fetch', fetcher)
  const view = await act(async () => render(<Invites />))
  return { ...view, fetcher }
}

test('expires a visible guest link without user interaction or another request', async () => {
  const { fetcher } = await openInventory()
  expect(screen.getByRole('button', { name: 'Revoke invite for alice' })).toBeVisible()
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(screen.getByText('expired')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Revoke invite for alice' })).toBeNull()
  expect(screen.getByText('Guest links').nextElementSibling).toHaveTextContent('0 available')
  expect(screen.getByRole('button', { name: 'Revoke owner card for owner' })).toBeVisible()
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('refreshes the displayed clock immediately when a suspended tab becomes visible', async () => {
  await openInventory()
  vi.setSystemTime(new Date(+epoch + 60000))
  await act(async () => fireEvent(document, new Event('visibilitychange')))
  expect(screen.getByText('expired')).toBeVisible()
  expect(screen.getByText('Guest links').nextElementSibling).toHaveTextContent('0 available')
})

test('cleans up scheduled clock work when leaving the page', async () => {
  const { unmount, fetcher } = await openInventory()
  unmount()
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => vi.advanceTimersByTimeAsync(60000))
  expect(fetcher).toHaveBeenCalledTimes(2)
})
