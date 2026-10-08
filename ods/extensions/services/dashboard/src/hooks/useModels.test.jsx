import { act, renderHook } from '@testing-library/react'
import { useModels } from './useModels'

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

test('a load request still in flight keeps waiting for its answer', async () => {
  stubServer(() => new Promise(() => {}))
  const { view } = await startLoad()

  server.lifecycle = null
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(view.result.current.error).toBeNull()
  expect(view.result.current.activationLoading).toBe('model-b')
  view.unmount()
})
