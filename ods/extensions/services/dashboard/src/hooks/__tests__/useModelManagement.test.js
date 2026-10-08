import { act, renderHook, waitFor } from '@testing-library/react'
import { useModels } from '../useModels'

const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const management = { managed: true, canActivate: true, canUnload: true, running: true }
const payload = extra => ({ models: [], odsMode: 'local', configuredMode: 'local', llmBackend: 'llama-server', hostRuntime: true, ...extra })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test.each([
  [undefined, false],
  [{ ...management, managed: false }, false],
  [{ ...management, managed: 'true' }, false],
  [{ ...management, canActivate: false, reason: 'Ownership is unavailable' }, false],
  [management, true],
  [{ ...management, canActivate: false, running: false }, false],
])('only confirmed managed capability permits external activation (%j)', async (capability, allowed) => {
  vi.stubGlobal('fetch', vi.fn(async () => response(payload({ modelManagement: capability }))))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.canActivateModels).toBe(allowed)
  if (!allowed) {
    await act(async () => { await result.current.loadModel('fixture') })
    expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  }
})

test.each([['stop', 'stopped', false], ['start', 'started', true]])('confirms %s and refreshes actual runtime state', async (operation, status, running) => {
  let current = { ...management, running: !running, canActivate: !running }
  let finish
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
    if (options?.method === 'POST') return await new Promise(resolve => { finish = () => { current = { ...current, running, canActivate: running }; resolve(response({ status })) } })
    return response(payload({ modelManagement: current }))
  }))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  let pending
  act(() => { pending = result.current[operation + 'Runtime']() })
  expect(result.current.runtimeActionLoading).toBe(operation)
  await act(async () => { await result.current[operation + 'Runtime']() })
  const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
  expect(posts).toHaveLength(1)
  expect(posts[0][0]).toBe('/api/models/runtime/' + operation)
  expect(JSON.parse(posts[0][1].body)).toEqual({})
  await act(async () => { finish(); await pending })
  expect(result.current.modelManagement.running).toBe(running)
  expect(result.current.canActivateModels).toBe(running)
  expect(result.current.runtimeActionLoading).toBeNull()
})

test('preserves unknown ownership during a transient proof failure and enables controls only after recovery', async () => {
  let capability = management
  vi.stubGlobal('fetch', vi.fn(async () => response(payload({ modelManagement: capability }))))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.canActivateModels).toBe(true))

  capability = { managed: null, canActivate: false, canUnload: false, running: false, reason: 'Runtime management could not be verified' }
  await act(async () => { await result.current.refresh() })
  expect(result.current.modelManagement.managed).toBeNull()
  expect(result.current.canActivateModels).toBe(false)
  expect(result.current.activationModeError).toBe(capability.reason)
  expect(result.current.activationModeError).not.toContain('Adopt')
  await act(async () => { await result.current.loadModel('fixture'); await result.current.stopRuntime() })
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)

  capability = management
  await act(async () => { await result.current.refresh() })
  expect(result.current.modelManagement.managed).toBe(true)
  expect(result.current.modelManagement.canUnload).toBe(true)
  expect(result.current.canActivateModels).toBe(true)
  expect(result.current.activationModeError).toBeNull()
})

test('keeps the runtime lock through the server budget and bounds an unresponsive start', async () => {
  let postSignal
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
    if (options?.method === 'POST') {
      postSignal = options.signal
      return new Promise(() => {})
    }
    return response(payload({ modelManagement: { ...management, running: false, canActivate: false } }))
  }))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  vi.useFakeTimers()
  let pending
  act(() => { pending = result.current.startRuntime() })
  await act(async () => { await vi.advanceTimersByTimeAsync(1200000) })
  expect(result.current.runtimeActionLoading).toBe('start')
  expect(postSignal.aborted).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(25000); await pending })
  expect(postSignal.aborted).toBe(true)
  expect(result.current.runtimeActionLoading).toBeNull()
  expect(result.current.error).toContain('timed out')
  expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
})

test('revokes stale runtime capabilities if the entire catalog request fails and restores them on success', async () => {
  let available = true
  vi.stubGlobal('fetch', vi.fn(async () => available
    ? response(payload({ modelManagement: management }))
    : response({}, 503)))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.canActivateModels).toBe(true))
  available = false
  await act(async () => { await result.current.refresh() })
  expect(result.current.modelManagement.managed).toBeNull()
  expect(result.current.canActivateModels).toBe(false)
  expect(result.current.activationModeError).not.toContain('Adopt')
  await act(async () => { await result.current.loadModel('fixture'); await result.current.startRuntime() })
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  available = true
  await act(async () => { await result.current.refresh() })
  expect(result.current.canActivateModels).toBe(true)
  expect(result.current.modelManagement.canUnload).toBe(true)
})

test.each([
  [response({ detail: { error: 'Portal is busy' } }, 409), 'Portal is busy'],
  [response({ status: 'starting' }), 'not confirmed'],
])('preserves runtime errors and rejects unconfirmed acknowledgements', async (postResponse, message) => {
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => options?.method === 'POST' ? postResponse : response(payload({ modelManagement: management }))))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  await act(async () => { await result.current.stopRuntime() })
  expect(result.current.error).toContain(message)
  expect(result.current.modelManagement.running).toBe(true)
})

test('never sends runtime controls for ordinary external installations', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response(payload({ modelManagement: { ...management, managed: false } }))))
  const { result } = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  await act(async () => { await result.current.stopRuntime(); await result.current.startRuntime() })
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
})
