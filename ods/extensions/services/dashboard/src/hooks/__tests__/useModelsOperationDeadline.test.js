import { act, renderHook } from '@testing-library/react'
import { useModels } from '../useModels'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); delete document.hidden })

describe.each([
  ['downloadModel', 15000, 'Download'],
  ['deleteModel', 35000, 'Delete'],
  ['benchmarkModel', 400000, 'Benchmark'],
])('%s request deadline', (method, budget, label) => {
  test.each(['headers', 'error body'])('settles a stalled %s and ignores late completion', async phase => {
    vi.useFakeTimers()
    let settle, rejectPending, signal
    const pending = new Promise((resolve, reject) => { settle = resolve; rejectPending = reject })
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (url === '/api/models') return { ok: true, json: async () => ({
        models: [{ id: 'target', status: method === 'downloadModel' ? 'available' : 'downloaded' }],
        gpu: null, currentModel: null, odsMode: 'local', configuredMode: 'local',
      }) }
      signal = options.signal
      if (phase === 'headers') signal?.addEventListener('abort', () => {
        const error = new Error('Request aborted')
        error.name = 'AbortError'
        rejectPending(error)
      }, { once: true })
      return phase === 'headers' ? pending : { ok: false, json: () => pending }
    }))
    let view
    await act(async () => { view = renderHook(() => useModels()) })
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    let done = false, failure
    await act(async () => {
      view.result.current[method]('target').catch(error => { failure = error }).finally(() => { done = true })
    })
    await act(async () => vi.advanceTimersByTimeAsync(budget - 1))
    expect(done).toBe(false)
    await act(async () => vi.advanceTimersByTimeAsync(1))
    expect(done).toBe(true)
    expect(signal.aborted).toBe(true)
    const message = failure?.message || view.result.current.error
    expect(message).toContain(label)
    expect(message).toMatch(method === 'downloadModel' ? /refresh|retry/i : /refresh/i)
    expect(view.result.current.actionLoadingModels).toEqual([])
    await act(async () => settle(phase === 'headers'
      ? { ok: true, json: async () => ({}) }
      : { detail: 'late error' }))
    expect(failure?.message || view.result.current.error).toBe(message)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})

test('confirms activation that completes after the old ten-minute deadline', async () => {
  vi.useFakeTimers()
  let loaded = false
  vi.stubGlobal('fetch', vi.fn(async (_url, options = {}) => {
    if (options.method === 'POST') return { ok: true }
    return { ok: true, json: async () => ({
      models: [{ id: 'target', status: loaded ? 'loaded' : 'downloaded' }],
      gpu: null, currentModel: loaded ? 'target' : null,
      activationReadyModel: loaded ? 'target' : null,
      odsMode: 'local', configuredMode: 'local',
    }) }
  }))
  let view
  await act(async () => { view = renderHook(() => useModels()) })
  Object.defineProperty(document, 'hidden', { configurable: true, value: true })
  let pending
  act(() => { pending = view.result.current.loadModel('target') })
  await act(async () => vi.advanceTimersByTimeAsync(720000))
  expect(view.result.current.actionLoading).toBe('target')
  expect(view.result.current.error).toBeNull()
  loaded = true
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); await pending })
  expect(view.result.current.actionLoading).toBeNull()
  expect(view.result.current.currentModel).toBe('target')
  expect(view.result.current.error).toBeNull()
})
