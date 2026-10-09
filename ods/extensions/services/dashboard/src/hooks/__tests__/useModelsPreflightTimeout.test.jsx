import { act, renderHook } from '@testing-library/react'
import { useModels } from '../useModels'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete document.hidden
})

test('retains activation through a failed status read and displays the fixed preflight refusal once', async () => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'hidden', { configurable: true, value: true })
  let statusAvailable = true
  let refuseActivation
  const message = 'ODS could not verify Docker service state, so it did not start this model switch. '
    + 'Wait until Models shows no operation in progress, refresh model status, then try again. '
    + 'If this repeats, check that Docker is responding.'
  vi.stubGlobal('fetch', vi.fn(async (_url, options = {}) => {
    if (options.method === 'POST') return new Promise(resolve => { refuseActivation = resolve })
    if (!statusAvailable) throw new TypeError('Status transport unavailable')
    return { ok: true, json: async () => ({
      models: [{ id: 'old', status: 'loaded' }, { id: 'target', status: 'downloaded' }],
      currentModel: 'old', activationReadyModel: 'old', odsMode: 'local', configuredMode: 'local',
    }) }
  }))
  let view
  await act(async () => { view = renderHook(() => useModels()) })
  await act(async () => view.result.current.refresh())
  statusAvailable = false
  let activation
  act(() => { activation = view.result.current.loadModel('target') })
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(view.result.current.activationLoading).toBe('target')
  statusAvailable = true
  await act(async () => {
    refuseActivation({ ok: false, status: 502, json: async () => ({
      detail: { code: 'model_preflight_inspection_timeout', error: message },
    }) })
    await vi.advanceTimersByTimeAsync(5000)
    await activation
  })
  expect(view.result.current.error).toBe(message)
  expect(view.result.current.activationLoading).toBeNull()
  expect(view.result.current.currentModel).toBe('old')
  expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  view.unmount()
})
