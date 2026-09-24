import { act, renderHook, waitFor } from '@testing-library/react'
import { useGPUDetailed } from '../useGPUDetailed'

describe('useGPUDetailed', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve({
      ok: url !== '/api/gpu/detailed',
      status: url === '/api/gpu/detailed' ? 503 : 200,
      json: () => Promise.resolve({}),
    })))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('surfaces a failed detailed snapshot instead of reporting a clean poll', async () => {
    const { result } = renderHook(() => useGPUDetailed())

    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.detailed).toBeNull()
    expect(result.current.error).toBe('GPU detail request failed (503)')
    expect(fetch).toHaveBeenCalledWith('/api/gpu/detailed', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  test('retains the last snapshot during an HTTP outage and clears the warning on recovery', async () => {
    vi.useFakeTimers()
    let failure = false
    let generation = 1
    fetch.mockImplementation(async () => ({ ok: !failure, status: failure ? 503 : 200, json: async () => ({ generation }) }))
    const { result, unmount } = renderHook(() => useGPUDetailed())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.detailed.generation).toBe(1)
    failure = true
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.error).toBe('GPU detail request failed (503)')
    expect(result.current.detailed.generation).toBe(1)
    failure = false
    generation = 2
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.detailed.generation).toBe(2)
    expect(result.current.error).toBeNull()
    unmount()
  })
})
