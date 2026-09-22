import { act, fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import FirstBoot from './FirstBoot' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

describe('FirstBoot admin session mint', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-firstboot-progress')
  })

  it('does not block completion when the optional session mint hangs', async () => {
    vi.useFakeTimers()
    globalThis.localStorage.setItem('ods-firstboot-progress', JSON.stringify({ step: 4, deviceName: 'ods', username: 'sam', stack: 'chat' }))
    const onComplete = vi.fn()
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') return Promise.resolve(response({ ready: false, reason: 'unavailable' }))
      if (url === '/api/setup/complete') return Promise.resolve(response({ success: true }))
      if (url === '/api/auth/admin-session') return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new globalThis.DOMException('Aborted', 'AbortError')), { once: true }))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    }))

    render(<FirstBoot onComplete={onComplete} />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(onComplete).toHaveBeenCalledTimes(1)
  })
})
