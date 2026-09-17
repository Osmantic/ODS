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

it('discards an inventory requested before activation even before the first new poll',async()=>{
  let finishOld,reads=0
  fetch.mockImplementation((url,options)=>{
    if(options?.method==='POST')return Promise.resolve({ok:true})
    if(++reads===2)return new Promise(resolve=>{finishOld=resolve})
    return Promise.resolve(snapshot('other'))
  })
  const {result,unmount}=renderHook(()=>useModels())
  await waitFor(()=>expect(result.current.loading).toBe(false))
  act(()=>{void result.current.refresh()})
  act(()=>{void result.current.loadModel('target')})
  await act(async()=>finishOld(snapshot('target')))
  expect(result.current.currentModel).toBe('other')
  expect(result.current.activationLoading).toBe('target')
  unmount()
})

it('confirms three sequential swaps against each requested context',async()=>{
  let selected='other'
  const windows={target:16384,other:32768}
  fetch.mockImplementation(async(url,options)=>{
    if(options?.method==='POST'){
      selected=url.includes('/target/')?'target':'other'
      expect(JSON.parse(options.body)).toEqual({context_length:windows[selected]})
      return {ok:true}
    }
    const data=await snapshot(selected).json()
    data.models=data.models.map(model=>({...model,contextLength:windows[model.id]}))
    return {ok:true,json:async()=>data}
  })
  const {result}=renderHook(()=>useModels())
  await waitFor(()=>expect(result.current.loading).toBe(false))
  Object.defineProperty(document,'hidden',{configurable:true,value:true})
  vi.useFakeTimers()
  for(const id of ['target','other','target']){
    let activation
    act(()=>{activation=result.current.loadModel(id,{contextLength:windows[id]})})
    await act(async()=>{await vi.advanceTimersByTimeAsync(5000);await activation})
    expect(result.current.activationReadyModel).toBe(id)
    expect(result.current.activationLoading).toBeNull()
    expect(result.current.error).toBeNull()
  }
  expect(fetch.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(3)
})
