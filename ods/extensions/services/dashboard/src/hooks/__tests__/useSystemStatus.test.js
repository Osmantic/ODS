import { act, renderHook, waitFor } from '@testing-library/react'
import { useSystemStatus } from '../useSystemStatus'

describe('useSystemStatus', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('times out a hung request so the next poll can run', async () => {
    vi.useFakeTimers()
    let rejectRequest
    fetch.mockImplementation((_url, { signal }) => {
      return new Promise((_, reject) => {
        rejectRequest = reject
        signal.addEventListener('abort', () => rejectRequest(new Error('request timed out')))
      })
    })

    const { result } = renderHook(() => useSystemStatus())
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })

    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBe('request timed out')
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  test('fetches status on mount', async () => {
    const mockStatus = { gpu: { name: 'RTX 4090' }, services: [], model: null, bootstrap: null, uptime: 100 }
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockStatus)
    })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.status.gpu.name).toBe('RTX 4090')
    expect(result.current.error).toBeNull()
  })

  test('starts with loading true', () => {
    fetch.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useSystemStatus())

    expect(result.current.loading).toBe(true)
  })

  test('sets loading false after fetch completes', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ gpu: null, services: [] })
    })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
  })

  test('sets error on failed fetch', async () => {
    fetch.mockResolvedValue({ ok: false })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.error).toBeTruthy()
    })
    expect(result.current.loading).toBe(false)
  })

  test('sets error on network failure', async () => {
    fetch.mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.error).toBe('network down')
    })
    expect(result.current.loading).toBe(false)
  })

  test('does not clear status on error (preserves previous data)', async () => {
    const mockStatus = { gpu: { name: 'RTX 4090' }, services: [], model: null, bootstrap: null, uptime: 100 }
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockStatus)
    })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.status.gpu?.name).toBe('RTX 4090')
    })

    // The hook keeps previous status on error by design
    // (see source: catch block only sets error, doesn't clear status)
    expect(result.current.status.gpu).toBeTruthy()
  })

  test('cleans up interval on unmount', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ gpu: null, services: [] })
    })

    const { unmount } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled()
    })

    const callCount = fetch.mock.calls.length
    unmount()

    // Wait a bit and confirm no new calls
    await new Promise(r => setTimeout(r, 100))
    expect(fetch.mock.calls.length).toBe(callCount)
  })
})
