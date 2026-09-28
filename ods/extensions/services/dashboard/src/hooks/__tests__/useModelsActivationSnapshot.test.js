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

it('confirms a context change when faster background reads overtake both nine-second confirmation requests',async()=>{
  let reads=0,contextLength=32768,settled=false
  fetch.mockImplementation(async(_url,options)=>{
    if(options?.method==='POST'){
      expect(JSON.parse(options.body)).toEqual({context_length:16384})
      contextLength=16384
      return {ok:true}
    }
    const index=++reads
    const data=await snapshot('target').json()
    data.models=data.models.map(model=>({...model,contextLength}))
    data.modelLifecycle=null
    data.odsMode=data.configuredMode='lemonade'
    data.llmBackend='lemonade'
    data.externalLemonade=true
    data.modelManagement={managed:true,canActivate:true,canUnload:true,running:true}
    // Initial inventory is immediate. Confirmation reads start at 0s and 9s;
    // background reads start every 2s and publish newer matching snapshots.
    if(index>1)await new Promise(resolve=>setTimeout(resolve,[2,7].includes(index)?9000:1000))
    return {ok:true,json:async()=>data}
  })
  Object.defineProperty(document,'hidden',{configurable:true,value:false})
  const {result}=renderHook(()=>useModels())
  await waitFor(()=>expect(result.current.loading).toBe(false))
  vi.useFakeTimers()
  let activation
  act(()=>{activation=result.current.loadModel('target',{contextLength:16384});activation.then(()=>{settled=true})})
  await act(async()=>{await vi.advanceTimersByTimeAsync(8000)})
  expect(result.current.models.find(model=>model.id==='target').contextLength).toBe(16384)
  expect(result.current.activationLoading).toBe('target')
  expect(settled).toBe(false)
  await act(async()=>{await vi.advanceTimersByTimeAsync(11000)})
  expect(settled).toBe(true)
  await activation
  expect(result.current.activationLoading).toBeNull()
  expect(result.current.actionLoadingModels).toEqual([])
  expect(result.current.modelManagement.canUnload).toBe(true)
  expect(result.current.error).toBeNull()
  expect(fetch.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1)
})
