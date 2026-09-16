import { act, fireEvent, render, screen } from '@testing-library/react'
import Invites from './Invites'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

test('recovers from stalled inventory JSON after HTTP headers arrive', async () => {
  vi.useFakeTimers()
  let stalled = true, signal
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url.endsWith('/list') && stalled) {
      signal = options.signal
      return { ok: true, status: 200, json: () => new Promise((_, reject) => {
        signal.addEventListener('abort', () => reject(new Error('Body aborted')), { once: true })
      }) }
    }
    return { ok: true, status: 200, json: async () => url.endsWith('/list') ? { tokens: [] } : { ready: true } }
  }))
  await act(async () => render(<Invites />))
  await act(async () => vi.advanceTimersByTimeAsync(8000))
  expect(signal.aborted).toBe(true)
  expect(screen.getByRole('alert')).toHaveTextContent('timed out')
  const refresh = screen.getByRole('button', { name: 'Refresh setup owner links' })
  expect(refresh).toBeEnabled()
  stalled = false
  await act(async () => fireEvent.click(refresh))
  expect(screen.getByText('No owner cards yet')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
})
