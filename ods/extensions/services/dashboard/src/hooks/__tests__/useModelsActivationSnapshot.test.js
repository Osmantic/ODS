import { renderHook, waitFor, act } from '@testing-library/react'
import { useModels } from '../useModels'

const snapshot = active => ({
  ok:true,
  json:async () => ({
    models:[
      {id:'target',status:active === 'target' ? 'loaded' : 'downloaded'},
      {id:'other',status:active === 'other' ? 'loaded' : 'downloaded'},
    ],
    currentModel:active, activationReadyModel:active,
    odsMode:'local', configuredMode:'local', llmBackend:'llama-server',
  }),
})

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete document.hidden
})

it('keeps activation pending when its poll is superseded by a newer inventory', async () => {
  let finishOld
  const old = new Promise(resolve => {finishOld = resolve})
  let reads = 0
  let active = 'other'
  fetch.mockImplementation((url, options) => {
    if (options?.method === 'POST') return Promise.resolve({ok:true})
    reads++
    return reads === 2 ? old : Promise.resolve(snapshot(active))
  })
  const {result} = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  Object.defineProperty(document, 'hidden', {configurable:true, value:true})
  vi.useFakeTimers()
  let activation
  act(() => {activation = result.current.loadModel('target')})
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(reads).toBe(2)
  await act(async () => {await result.current.refresh()})
  await act(async () => {finishOld(snapshot('target'))})
  expect(result.current.currentModel).toBe('other')
  expect(result.current.activationLoading).toBe('target')
  expect(result.current.error).toBeNull()

  active = 'target'
  await act(async () => {await vi.advanceTimersByTimeAsync(5000); await activation})
  expect(result.current.activationLoading).toBeNull()
  expect(result.current.currentModel).toBe('target')
  expect(result.current.error).toBeNull()
})

it('reports an unconfirmed activation when the final snapshot no longer matches', async () => {
  let reads = 0
  fetch.mockImplementation((url, options) => {
    if (options?.method === 'POST') return Promise.resolve({ok:true})
    reads++
    return Promise.resolve(snapshot(reads === 2 ? 'target' : 'other'))
  })
  const {result} = renderHook(() => useModels())
  await waitFor(() => expect(result.current.loading).toBe(false))
  Object.defineProperty(document, 'hidden', {configurable:true, value:true})
  vi.useFakeTimers()
  let activation
  act(() => {activation = result.current.loadModel('target')})
  await act(async () => {await vi.advanceTimersByTimeAsync(5000); await activation})
  expect(result.current.currentModel).toBe('other')
  expect(result.current.activationLoading).toBeNull()
  expect(result.current.error).toMatch(/Could not confirm activation of target/)
})
