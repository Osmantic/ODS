import { act, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import FirstBoot from './FirstBoot' // eslint-disable-line no-unused-vars

describe('FirstBoot owner-card readiness', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-firstboot-progress')
  })

  it('shows owner-card unavailability when readiness times out', async () => {
    vi.useFakeTimers()
    globalThis.localStorage.setItem('ods-firstboot-progress', JSON.stringify({ step: 4, deviceName: 'ods', username: 'sam', stack: 'chat' }))
    vi.stubGlobal('fetch', vi.fn((_, options) => new Promise((_, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })))

    render(<FirstBoot onComplete={vi.fn()} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(screen.getByText(/Owner-card status unavailable/)).toBeInTheDocument()
  })
})
