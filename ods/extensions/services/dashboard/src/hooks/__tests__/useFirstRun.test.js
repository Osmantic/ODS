import { act, renderHook } from '@testing-library/react'
import { useFirstRun } from '../useFirstRun'

describe('useFirstRun', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('recovers from a hung setup-status request', async () => {
    vi.useFakeTimers()
    let rejectRequest
    vi.stubGlobal('fetch', vi.fn((_url, { signal }) => new Promise((_, reject) => {
      rejectRequest = reject
      signal.addEventListener('abort', () => rejectRequest(new Error('request timed out')))
    })))

    const { result } = renderHook(() => useFirstRun())
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })

    expect(result.current.loading).toBe(false)
    expect(result.current.firstRun).toBe(false)
    expect(result.current.error).toBe('request timed out')
  })
})
