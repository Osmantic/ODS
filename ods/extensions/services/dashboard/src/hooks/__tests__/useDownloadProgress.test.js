import { renderHook, waitFor, act } from '@testing-library/react'
import { useDownloadProgress } from '../useDownloadProgress'

// Shadow jsdom's Document.prototype.hidden getter on the instance; deleting
// the own property in afterEach restores the prototype behavior.
const setDocumentHidden = (hidden) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('useDownloadProgress', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete document.hidden
  })

  test('sets isDownloading when status is downloading', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        status: 'downloading',
        model: 'test-model',
        percent: 50,
        bytesDownloaded: 5e9,
        bytesTotal: 10e9,
      })
    })

    const { result } = renderHook(() => useDownloadProgress())

    await waitFor(() => {
      expect(result.current.isDownloading).toBe(true)
    })
    expect(result.current.progress.percent).toBe(50)
    expect(result.current.progress.model).toBe('test-model')
  })

  test('clamps progress percentage at 100 when downloaded bytes exceed total', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        status: 'downloading',
        model: 'test-model',
        bytesDownloaded: 12e9,
        bytesTotal: 10e9,
      })
    })

    const { result } = renderHook(() => useDownloadProgress())

    await waitFor(() => {
      expect(result.current.isDownloading).toBe(true)
    })
    expect(result.current.progress.percent).toBe(100)
  })

  test('clears progress when status is complete', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        status: 'complete',
        model: 'test-model',
        updatedAt: '2026-05-16T12:00:00Z'
      })
    })

    const { result } = renderHook(() => useDownloadProgress())

    await waitFor(() => {
      expect(result.current.completedDownload).toEqual({
        status: 'complete',
        model: 'test-model',
        updatedAt: '2026-05-16T12:00:00Z'
      })
    })
    expect(result.current.isDownloading).toBe(false)
    expect(result.current.progress).toBeNull()
  })

  test.each(['failed', 'error', 'cancelled'])('keeps %s status visible across a later idle poll', async (status) => {
    fetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          status,
          model: 'test-model',
          message: `${status} detail`,
        })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'idle' })
      })

    const { result } = renderHook(() => useDownloadProgress())

    await waitFor(() => expect(result.current.progress?.status).toBe(status))
    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.isDownloading).toBe(false)
    expect(result.current.progress).toMatchObject({
      status,
      model: 'test-model',
      error: `${status} detail`,
    })
  })

  test('does not let an older idle response overwrite newer download progress', async () => {
    const olderRequest = deferred()
    const newerRequest = deferred()
    fetch
      .mockImplementationOnce(() => olderRequest.promise)
      .mockImplementationOnce(() => newerRequest.promise)

    const { result } = renderHook(() => useDownloadProgress())
    let newerRefresh
    act(() => {
      newerRefresh = result.current.refresh()
    })

    await act(async () => {
      newerRequest.resolve({
        ok: true,
        json: () => Promise.resolve({
          status: 'downloading',
          model: 'new-model.gguf',
          bytesDownloaded: 5,
          bytesTotal: 10,
        }),
      })
      await newerRefresh
    })
    expect(result.current.isDownloading).toBe(true)
    expect(result.current.progress).toMatchObject({
      model: 'new-model.gguf',
      status: 'downloading',
      percent: 50,
    })

    await act(async () => {
      olderRequest.resolve({
        ok: true,
        json: () => Promise.resolve({ status: 'idle' }),
      })
      await olderRequest.promise
      await Promise.resolve()
    })

    expect(result.current.isDownloading).toBe(true)
    expect(result.current.progress).toMatchObject({
      model: 'new-model.gguf',
      status: 'downloading',
    })
  })

  test('cancelDownload posts to the cancel endpoint and refreshes terminal status', async () => {
    let cancelled = false
    fetch.mockImplementation((url, options) => {
      if (options?.method === 'POST') {
        cancelled = true
        return Promise.resolve({ ok: true })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(cancelled
          ? { status: 'cancelled', model: 'test-model' }
          : {
              status: 'downloading',
              model: 'test-model',
              bytesDownloaded: 1,
              bytesTotal: 10,
            })
      })
    })

    const { result } = renderHook(() => useDownloadProgress())
    await waitFor(() => expect(result.current.isDownloading).toBe(true))

    await act(async () => {
      await result.current.cancelDownload()
    })

    expect(fetch).toHaveBeenCalledWith('/api/models/download/cancel', { method: 'POST' })
    expect(result.current.isDownloading).toBe(false)
    expect(result.current.progress).toMatchObject({
      status: 'cancelled',
      model: 'test-model',
      error: 'Download cancelled',
    })
  })

  test('keeps active progress and exposes an error when cancellation fails', async () => {
    const cancelRequest = deferred()
    fetch.mockImplementation((_url, options) => {
      if (options?.method === 'POST') return cancelRequest.promise
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          status: 'downloading',
          model: 'test-model',
          bytesDownloaded: 1,
          bytesTotal: 10,
        }),
      })
    })

    const { result } = renderHook(() => useDownloadProgress())
    await waitFor(() => expect(result.current.isDownloading).toBe(true))

    let cancelPromise
    act(() => {
      cancelPromise = result.current.cancelDownload()
    })
    await waitFor(() => expect(result.current.isCancelling).toBe(true))

    await act(async () => {
      cancelRequest.resolve({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ detail: 'The host agent did not accept cancellation.' }),
      })
      await cancelPromise
    })

    expect(result.current.isCancelling).toBe(false)
    expect(result.current.cancelError).toBe('The host agent did not accept cancellation.')
    expect(result.current.isDownloading).toBe(true)
    expect(result.current.progress).toMatchObject({
      status: 'downloading',
      model: 'test-model',
      percent: 10,
    })
  })

  test('pauses idle polling while the tab is hidden and refreshes on visibilitychange', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'idle' })
    })

    vi.useFakeTimers()
    try {
      renderHook(() => useDownloadProgress())
      await act(async () => {})
      expect(fetch).toHaveBeenCalledTimes(1)

      setDocumentHidden(true)
      await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
      expect(fetch).toHaveBeenCalledTimes(1)

      setDocumentHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
      expect(fetch).toHaveBeenCalledTimes(2)

      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(fetch).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  test('formatBytes formats GB/MB/KB correctly', () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'idle' })
    })

    const { result } = renderHook(() => useDownloadProgress())

    expect(result.current.formatBytes(5e9)).toBe('4.66 GB')
    expect(result.current.formatBytes(5e6)).toBe('4.8 MB')
    expect(result.current.formatBytes(5000)).toBe('5 KB')
    expect(result.current.formatBytes(512)).toBe('512 B')
    expect(result.current.formatBytes(0)).toBe('0 B')
    expect(result.current.formatBytes(null)).toBe('0 B')
  })

  test('formatEta formats minutes and seconds', () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'idle' })
    })

    const { result } = renderHook(() => useDownloadProgress())

    expect(result.current.formatEta(90)).toBe('1m 30s')
    expect(result.current.formatEta(30)).toBe('30s')
    // Fractional eta (bytes-remaining / rate) must not leak float noise.
    expect(result.current.formatEta(90.7)).toBe('1m 30s')
    expect(result.current.formatEta(30.9)).toBe('30s')
    expect(result.current.formatEta(null)).toBe('calculating...')
    expect(result.current.formatEta('calculating...')).toBe('calculating...')
  })
})
