import { act, renderHook, waitFor } from '@testing-library/react'
import { useGPUDetailed } from '../useGPUDetailed'

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

describe('useGPUDetailed', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  })

  test('loads initial GPU state while the document is hidden', async () => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response({ gpus: [{ name: 'GPU' }] }))
      .mockResolvedValueOnce(response({ samples: [] }))
      .mockResolvedValueOnce(response({ links: [] })))

    const { result } = renderHook(() => useGPUDetailed())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(result.current.detailed.gpus[0].name).toBe('GPU')
    expect(result.current.error).toBeNull()
  })

  test('pauses after hidden initial data and resumes immediately when visible', async () => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ gpus: [] })))
    const { unmount } = renderHook(() => useGPUDetailed())
    await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
    expect(fetch).toHaveBeenCalledTimes(3)
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(fetch).toHaveBeenCalledTimes(6)
    unmount()
  })
})
