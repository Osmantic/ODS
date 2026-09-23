import { act, fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import FirstBoot from './FirstBoot' // eslint-disable-line no-unused-vars

describe('FirstBoot finish operations', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-firstboot-progress')
  })

  it('surfaces a recoverable error when setup completion times out', async () => {
    vi.useFakeTimers()
    globalThis.localStorage.setItem('ods-firstboot-progress', JSON.stringify({ step: 4, deviceName: 'ods', username: 'sam', stack: 'chat' }))
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') return Promise.resolve({ ok: true, json: async () => ({ ready: false, reason: 'unavailable' }) })
      if (url === '/api/setup/complete') return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new globalThis.DOMException('Aborted', 'AbortError')), { once: true }))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    }))

    render(<FirstBoot onComplete={vi.fn()} />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }))
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(screen.getByText('This operation timed out. Please retry Finish.')).toBeInTheDocument()
  })
})
