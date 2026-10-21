import { act, renderHook } from '@testing-library/react'
import { useDownloadProgress } from '../useDownloadProgress'

const active = { status: 'downloading', model: 'model.gguf', bytesDownloaded: 5, bytesTotal: 10 }
const response = data => ({ ok: true, json: async () => data })
let stalledSignal
let finishStalled

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); delete document.hidden })

function stallBody(_url, { signal } = {}) {
  stalledSignal = signal
  return Promise.resolve({ ok: true, json: () => new Promise((resolve, reject) => {
    finishStalled = resolve
    signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  }) })
}

test('one stalled status body has a deadline, retains progress, and polling recovers', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(active))
    .mockImplementationOnce(stallBody).mockResolvedValue(response({ status: 'complete', model: active.model, updatedAt: '2026-10-21T00:00:00Z' })))
  const { result } = renderHook(() => useDownloadProgress())
  await act(async () => {})
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(fetch).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTimeAsync(14000))
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(result.current.progress.percent).toBe(50)
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(stalledSignal.aborted).toBe(true)
  expect(result.current.statusError).toMatch(/timed out/i)
  expect(result.current.isDownloading).toBe(true)
  expect(result.current.progress.percent).toBe(50)
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(result.current.statusError).toBeNull()
  expect(result.current.isDownloading).toBe(false)
  expect(result.current.completedDownload.model).toBe(active.model)
})

test('unmount aborts a status body and releases its deadline', async () => {
  vi.stubGlobal('fetch', vi.fn(stallBody))
  const { unmount } = renderHook(() => useDownloadProgress())
  await act(async () => {})
  unmount()
  expect(stalledSignal.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => finishStalled(active))
})

test('explicit refresh aborts an older body and the new receipt wins', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(stallBody).mockResolvedValue(response(active)))
  const { result } = renderHook(() => useDownloadProgress())
  await act(async () => {})
  await act(async () => result.current.refresh())
  expect(stalledSignal.aborted).toBe(true)
  expect(result.current.statusError).toBeNull()
  expect(result.current.progress.percent).toBe(50)
  await act(async () => finishStalled({ status: 'idle' }))
  expect(result.current.progress.percent).toBe(50)
})
