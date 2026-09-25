import { act, renderHook } from '@testing-library/react'
import { useDownloadProgress } from '../useDownloadProgress'

const active = { status: 'downloading', model: 'model.gguf', bytesDownloaded: 5, bytesTotal: 10 }
const json = (data) => ({ ok: true, json: async () => data })
const untilAbort = (signal) => new Promise((_, reject) => {
  signal.addEventListener('abort', () => reject(new window.DOMException('Aborted', 'AbortError')), { once: true })
})

describe('download cancellation acknowledgements', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn())
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    delete document.hidden
  })

  test.each(['headers', 'error body'])('releases the guard after stalled %s and permits a deliberate retry', async (stage) => {
    let posts = 0
    let stalledSignal
    fetch.mockImplementation((_url, options) => {
      if (options?.method !== 'POST') return Promise.resolve(json(active))
      posts += 1
      if (posts > 1) return Promise.resolve(json({}))
      stalledSignal = options.signal
      // The baseline has no signal; keep its request pending until the assertion fails.
      const pending = stalledSignal ? untilAbort(stalledSignal) : new Promise(() => {})
      return stage === 'headers' ? pending : Promise.resolve({ ok: false, json: () => pending })
    })
    const { result } = renderHook(() => useDownloadProgress())
    await act(async () => {})
    let cancellation
    act(() => { cancellation = result.current.cancelDownload() })
    await act(async () => { await result.current.cancelDownload() })
    expect(posts).toBe(1)
    expect(result.current.isCancelling).toBe(true)

    await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
    expect(result.current.isCancelling).toBe(false)
    await cancellation
    expect(stalledSignal.aborted).toBe(true)
    expect(result.current.cancelError).toMatch(/not acknowledged/i)
    expect(result.current.progress).toMatchObject({ model: 'model.gguf', status: 'downloading', percent: 50 })

    await act(async () => { await result.current.cancelDownload() })
    expect(posts).toBe(2)
    expect(result.current.cancelError).toBeNull()
  })

  test('an acknowledged cancellation does not hold its guard while the status refresh stalls', async () => {
    let reads = 0
    let resolveStatus
    const status = new Promise(resolve => { resolveStatus = resolve })
    fetch.mockImplementation((_url, options) => {
      if (options?.method === 'POST') return Promise.resolve(json({}))
      reads += 1
      return reads === 1 ? Promise.resolve(json(active)) : status
    })
    const { result } = renderHook(() => useDownloadProgress())
    await act(async () => {})
    let cancellation
    act(() => { cancellation = result.current.cancelDownload() })
    await act(async () => {})
    expect(result.current.isCancelling).toBe(false)
    expect(result.current.progress.status).toBe('downloading')
    expect(result.current.cancelError).toBeNull()
    await act(async () => {
      resolveStatus(json({ status: 'cancelled', model: 'model.gguf' }))
      await cancellation
    })
    expect(result.current.progress.status).toBe('cancelled')
  })
})
