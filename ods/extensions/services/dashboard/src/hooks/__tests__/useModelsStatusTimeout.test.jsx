import { act, renderHook } from '@testing-library/react'
import { useModels } from '../useModels'

const payload = () => ({
  models: [{ id: 'old', status: 'loaded' }, { id: 'target', status: 'downloaded' }],
  currentModel: 'old',
  activationReadyModel: 'old',
  odsMode: 'local',
  configuredMode: 'local',
  llmBackend: 'llama-server',
})
const response = () => ({ ok: true, json: async () => payload() })

function stall(signal) {
  return new Promise((_, reject) => signal.addEventListener('abort', () => {
    reject(new DOMException('signal is aborted without reason', 'AbortError'))
  }, { once: true }))
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'hidden', { configurable: true, value: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete document.hidden
})

test.each(['headers', 'body'])('describes an owned status timeout during %s and recovers on fresh status', async phase => {
  let slow = false
  vi.stubGlobal('fetch', vi.fn((_url, options) => slow
    ? phase === 'headers'
      ? stall(options.signal)
      : Promise.resolve({ ok: true, json: () => stall(options.signal) })
    : Promise.resolve(response())))
  let view
  await act(async () => { view = renderHook(() => useModels({ observe: false })) })
  await act(async () => view.result.current.refresh())
  slow = true
  let pending
  act(() => { pending = view.result.current.refresh() })
  await act(async () => vi.advanceTimersByTimeAsync(29999))
  expect(view.result.current.error).toBeNull()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1)
    await pending
  })
  expect(view.result.current.error).toContain('Could not refresh model status within 30 seconds')
  expect(view.result.current.error).toContain('wait for status confirmation')
  expect(view.result.current.error).not.toMatch(/aborted|AbortError/)
  expect(view.result.current.currentModel).toBe('old')
  slow = false
  await act(async () => view.result.current.refresh())
  expect(view.result.current.error).toBeNull()
  view.unmount()
})

test('keeps activation owned across status timeout and retains the later host refusal without retrying', async () => {
  let slow = false
  let resolvePost
  let postSignal
  vi.stubGlobal('fetch', vi.fn((_url, options = {}) => {
    if (options.method === 'POST') {
      postSignal = options.signal
      return new Promise(resolve => { resolvePost = resolve })
    }
    return slow ? stall(options.signal) : Promise.resolve(response())
  }))
  let view
  await act(async () => { view = renderHook(() => useModels()) })
  await act(async () => view.result.current.refresh())
  slow = true
  let activation
  act(() => { activation = view.result.current.loadModel('target', { contextLength: 32768 }) })
  await act(async () => vi.advanceTimersByTimeAsync(35000))
  expect(view.result.current.activationLoading).toBe('target')
  expect(postSignal.aborted).toBe(false)
  expect(view.result.current.error).toContain('Could not refresh model status within 30 seconds')
  slow = false
  await act(async () => {
    resolvePost({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'Could not inspect optional container ods-litellm: inspection timed out' }),
    })
    await vi.advanceTimersByTimeAsync(5000)
    await activation
  })
  expect(view.result.current.activationLoading).toBeNull()
  expect(view.result.current.currentModel).toBe('old')
  expect(view.result.current.error).toBe('Could not inspect optional container ods-litellm: inspection timed out')
  expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  view.unmount()
})

test('navigation cancellation stays silent and does not submit another activation', async () => {
  let slow = false
  vi.stubGlobal('fetch', vi.fn((_url, options = {}) => options.method === 'POST' || slow
    ? stall(options.signal)
    : Promise.resolve(response())))
  let view
  await act(async () => { view = renderHook(() => useModels()) })
  await act(async () => view.result.current.refresh())
  slow = true
  let activation
  act(() => { activation = view.result.current.loadModel('target') })
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  await act(async () => {
    view.unmount()
    await activation
  })
  expect(view.result.current.error).toBeNull()
  expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  expect(vi.getTimerCount()).toBe(0)
})

test('does not relabel a real transport failure as the request deadline', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Network connection failed'))))
  let view
  await act(async () => { view = renderHook(() => useModels({ observe: false })) })
  await act(async () => view.result.current.refresh())
  expect(view.result.current.error).toBe('Network connection failed')
  view.unmount()
})
