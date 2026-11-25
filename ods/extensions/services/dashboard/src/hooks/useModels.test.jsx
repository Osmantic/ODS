import { act, renderHook } from '@testing-library/react'
import { forgetLastCatalog, useModels } from './useModels'

const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const activating = { active: true, operation: 'model_activation', target: 'model-b' }
let server

function snapshot() {
  return {
    models: [{ id: 'model-a', status: 'loaded' }, { id: 'model-b', status: 'downloaded' }],
    currentModel: server.current,
    activationReadyModel: server.current,
    odsMode: 'local',
    configuredMode: 'local',
    llmBackend: 'llama-server',
    modelLifecycle: server.lifecycle,
  }
}

// /api/models reflects `server`; the load request gets `server.load`.
function stubServer(load) {
  server = { current: 'model-a', lifecycle: activating, load }
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/models') return reply(200, snapshot())
    if (url === '/api/models/model-b/load') return server.load()
    throw new Error(`unexpected request ${url}`)
  }))
}

const joined = () => reply(409, { detail: { message: 'Another model activation is in progress', activeModelId: 'model-b' } })

async function startLoad() {
  const view = renderHook(() => useModels())
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  let load
  await act(async () => {
    load = view.result.current.loadModel('model-b')
    await vi.advanceTimersByTimeAsync(5000)
  })
  return { view, load }
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

test('a joined activation that ends without the model reports it instead of waiting out the deadline', async () => {
  stubServer(joined)
  const { view, load } = await startLoad()
  expect(view.result.current.activationLoading).toBe('model-b')

  server.lifecycle = null
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000)
    await load
  })
  expect(view.result.current.error).toMatch(/activation of model-b ended without loading it/)
  expect(view.result.current.activationLoading).toBeNull()
})

test('a joined activation that loads the model is confirmed', async () => {
  stubServer(joined)
  const { view, load } = await startLoad()

  server.lifecycle = null
  server.current = 'model-b'
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000)
    await load
  })
  expect(view.result.current.error).toBeNull()
  expect(view.result.current.currentModel).toBe('model-b')
})

test('an unsupported architecture shows the refusal and keeps the current model without joining an activation', async () => {
  const message = "Model architecture 'dspark' is not supported by this runtime. The active model was not changed."
  stubServer(() => reply(409, { detail: {
    code: 'runtime_architecture_unsupported', error: message, requestedModelId: 'model-b',
  } }))
  server.lifecycle = null
  const { view, load } = await startLoad()
  await act(async () => { await load })
  expect(view.result.current.error).toBe(message)
  expect(view.result.current.currentModel).toBe('model-a')
  expect(view.result.current.activationLoading).toBeNull()
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/load'))).toHaveLength(1)
})

test('a load request still in flight keeps waiting for its answer', async () => {
  stubServer(() => new Promise(() => {}))
  const { view } = await startLoad()

  server.lifecycle = null
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(view.result.current.error).toBeNull()
  expect(view.result.current.activationLoading).toBe('model-b')
  view.unmount()
})

test('a caller that asks starts from the last catalog and holds actions until its own read lands', async () => {
  forgetLastCatalog()
  server = { current: 'model-a', lifecycle: null }
  let pending = null
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url !== '/api/models') throw new Error(`unexpected request ${url}`)
    return pending || reply(200, snapshot())
  }))
  const first = renderHook(() => useModels({ reuseLastCatalog: true }))
  expect(first.result.current.loading).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(first.result.current.currentModel).toBe('model-a')
  expect(first.result.current.catalogConfirmed).toBe(true)
  first.unmount()

  let release
  pending = new Promise(resolve => { release = resolve })
  server.current = 'model-b'
  const again = renderHook(() => useModels({ reuseLastCatalog: true }))
  expect(again.result.current.loading).toBe(false)
  expect(again.result.current.currentModel).toBe('model-a')
  expect(again.result.current.catalogConfirmed).toBe(false)

  await act(async () => {
    release(reply(200, snapshot()))
    await vi.advanceTimersByTimeAsync(0)
  })
  expect(again.result.current.currentModel).toBe('model-b')
  expect(again.result.current.catalogConfirmed).toBe(true)
})

test('a failed fresh read keeps the shown catalog held', async () => {
  forgetLastCatalog()
  server = { current: 'model-a', lifecycle: null }
  let fail = false
  vi.stubGlobal('fetch', vi.fn(async () => (fail ? reply(503, {}) : reply(200, snapshot()))))
  const first = renderHook(() => useModels({ reuseLastCatalog: true }))
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  first.unmount()

  fail = true
  const again = renderHook(() => useModels({ reuseLastCatalog: true }))
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(again.result.current.error).toBe('Failed to fetch models')
  expect(again.result.current.currentModel).toBe('model-a')
  expect(again.result.current.catalogConfirmed).toBe(false)
})

test('a caller that does not ask still starts empty', async () => {
  forgetLastCatalog()
  server = { current: 'model-a', lifecycle: null }
  vi.stubGlobal('fetch', vi.fn(async () => reply(200, snapshot())))
  const first = renderHook(() => useModels())
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  first.unmount()

  const other = renderHook(() => useModels())
  expect(other.result.current.loading).toBe(true)
  expect(other.result.current.models).toEqual([])
  expect(other.result.current.catalogConfirmed).toBe(true)
})
