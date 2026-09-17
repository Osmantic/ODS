import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import PortalModelSelector,{modelDisplayName} from './PortalModelSelector'

const old='community/qwen-4b',target='Qwen/Qwen 3.5 2B'
const technical='extra.hf-HauhauCS-Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M-e4a219e5.gguf'
const inventory=[
  {id:old,name:'Qwen3.5-4B-Uncensored-HauhauCS-Aggressive · Q4_K_M',status:'loaded',fitsVram:true,contextLength:32768,quantization:'Q4_K_M'},
  {id:target,name:'Qwen 3.5 2B',status:'downloaded',fitsVram:true,contextLength:8192,quantization:'Q4_K_M'},
  {id:'remote/huge',name:'Large 100B',status:'downloaded',fitsVram:false,contextLength:32768},
  {id:'not-installed',name:'Install me',status:'available',fitsVram:true},
]
let current,ready,postResult,lifecycle
const payload=()=>({models:inventory,currentModel:current,loadedModel:technical,activationReadyModel:ready,odsMode:'lemonade',configuredMode:'lemonade',llmBackend:'lemonade',modelLifecycle:lifecycle,gpu:{vramTotal:8}})
const view=props=><MemoryRouter><PortalModelSelector activeModel={technical} runtimeSource="local-switchboard" {...props}/></MemoryRouter>
beforeEach(()=>{
  current=old;ready=old;postResult={ok:true};lifecycle=null
  vi.stubGlobal('fetch',vi.fn(async (url,options)=>options?.method==='POST'?postResult:{ok:true,json:async()=>payload()}))
})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})
const posts=()=>fetch.mock.calls.filter(([,options])=>options?.method==='POST')

it('keeps recovery accessible for an unknown model source and observes it after closing the menu',async()=>{
  let resolveRecovery
  const state={pending:true,phase:'applied',transactionId:'a'.repeat(64)}
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{
    if(url==='/api/models/recovery') {
      if(options?.method==='POST')return await new Promise(resolve=>{resolveRecovery=resolve})
      return {ok:true,json:async()=>state}
    }
    return {ok:true,json:async()=>payload()}
  }))
  const switching=vi.fn(),settled=vi.fn()
  render(view({runtimeSource:undefined,onSwitchingChange:switching,onSettled:settled}))
  await open()
  fireEvent.click(await screen.findByRole('button',{name:'Recover model switch'}))
  expect(switching).toHaveBeenLastCalledWith(true)
  fireEvent.keyDown(window,{key:'Escape'})
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(switching).toHaveBeenLastCalledWith(true)
  expect(posts()).toHaveLength(1)
  expect(posts()[0][0]).toBe('/api/models/recovery')
  state.pending=false;state.phase='completed'
  await act(async()=>resolveRecovery({ok:true,json:async()=>state}))
  expect(switching).toHaveBeenLastCalledWith(false)
  expect(settled).toHaveBeenCalledOnce()
})
async function open() {
  const button=await screen.findByRole('button',{name:'Choose model: Qwen 3.5 4B'})
  fireEvent.click(button)
  await screen.findByRole('menuitemradio',{name:/Qwen 3.5 2B/})
  return button
}

it('shows readable names and an installed-model menu, with an explicit switch confirmation',async()=>{
  render(view())
  const trigger=await open()
  expect(screen.queryByText(technical)).toBeNull()
  expect(screen.getByRole('menuitemradio',{name:/Uncensored/})).toHaveAttribute('aria-checked','true')
  expect(screen.queryByRole('menuitemradio',{name:/Install me/})).toBeNull()
  expect(screen.getByRole('menuitemradio',{name:/Large 100B/})).toBeDisabled()
  fireEvent.click(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/}))
  expect(screen.getByText('This changes the active model across ODS.')).toBeVisible()
  expect(screen.getByRole('button',{name:'Cancel'})).toHaveFocus()
  expect(posts()).toHaveLength(0)
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}))
  fireEvent.keyDown(screen.getByRole('menuitemradio',{name:/Uncensored/}),{key:'ArrowDown'})
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toHaveFocus()
  fireEvent.keyDown(window,{key:'Escape'})
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button',{name:'Choose model: Qwen 3.5 4B'})).toHaveFocus()
  expect(posts()).toHaveLength(0)
})

it('accepts a measured native profile only at its proven context while retaining the switch confirmation',async()=>{
  const candidate=inventory[2]
  candidate.activationSupport={available:true,source:'measured-native',mode:'native-profile',contextLength:candidate.contextLength}
  try {
    render(view())
    await open()
    const option=screen.getByRole('menuitemradio',{name:/Large 100B/})
    expect(option).toBeEnabled()
    fireEvent.click(option)
    expect(screen.getByRole('button',{name:'Switch model',exact:true})).toBeEnabled()
    expect(posts()).toHaveLength(0)
  } finally {delete candidate.activationSupport}
})

it.each([
  {available:true,source:'estimate',mode:'native-profile',contextLength:32768},
  {available:true,source:'measured-native',mode:'native-profile',contextLength:16384},
  {available:false,source:'measured-native',mode:'native-profile',contextLength:32768},
])('does not bypass memory protection for an unproven or mismatched native profile',async activationSupport=>{
  inventory[2].activationSupport=activationSupport
  try {
    render(view())
    await open()
    expect(screen.getByRole('menuitemradio',{name:/Large 100B/})).toBeDisabled()
    expect(posts()).toHaveLength(0)
  } finally {delete inventory[2].activationSupport}
})

it('uses the real encoded activation route and waits for readiness instead of marking a POST as success',async()=>{
  vi.useFakeTimers()
  const switching=vi.fn(),settled=vi.fn()
  render(view({onSwitchingChange:switching,onSettled:settled}))
  await act(async()=>{})
  fireEvent.click(screen.getByRole('button',{name:'Choose model: Qwen 3.5 4B'}))
  await act(async()=>{})
  fireEvent.click(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/}))
  fireEvent.click(screen.getByRole('button',{name:'Switch model',exact:true}))
  expect(posts()).toHaveLength(1)
  expect(posts()[0][0]).toBe(`/api/models/${encodeURIComponent(target)}/load`)
  expect(JSON.parse(posts()[0][1].body)).toEqual({context_length:8192})
  expect(posts()[0][1]).toMatchObject({method:'POST',signal:expect.any(AbortSignal)})
  expect(switching).toHaveBeenLastCalledWith(true)
  current=target;ready=null
  await act(async()=>{await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toHaveAttribute('aria-checked','false')
  expect(settled).not.toHaveBeenCalled()
  ready=target
  await act(async()=>{await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toHaveAttribute('aria-checked','true')
  expect(switching).toHaveBeenLastCalledWith(false)
  expect(settled).toHaveBeenCalledOnce()
  expect(posts()).toHaveLength(1)
})

it('keeps the current model selected when the host rejects a swap during work',async()=>{
  vi.useFakeTimers()
  postResult={ok:false,status:409,json:async()=>({detail:{code:'pixel_chat_active',message:'Portal is working. Stop the active response before changing models.'}})}
  render(view())
  await act(async()=>{})
  fireEvent.click(screen.getByRole('button',{name:'Choose model: Qwen 3.5 4B'}))
  await act(async()=>{})
  fireEvent.click(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/}))
  fireEvent.click(screen.getByRole('button',{name:'Switch model',exact:true}))
  await act(async()=>{await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('alert')).toHaveTextContent('Portal is working.')
  expect(screen.getByRole('menuitemradio',{name:/Uncensored/})).toHaveAttribute('aria-checked','true')
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toHaveAttribute('aria-checked','false')
})

it.each([true,false])('clears an earlier activation error only after confirmed recovery (success=%s)',async succeeds=>{
  vi.useFakeTimers()
  let recoveryState={pending:false,phase:'idle',transactionId:null}
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{
    if(url==='/api/models/recovery') {
      if(options?.method==='POST') {
        return {ok:succeeds,status:succeeds?200:409,json:async()=>succeeds
          ? {pending:false,phase:'completed',transactionId:'a'.repeat(64),outcome:'rollback'}
          : {...recoveryState,reason:'model-recovery-proof-required'}}
      }
      return {ok:true,json:async()=>recoveryState}
    }
    if(options?.method==='POST') {
      recoveryState={pending:true,phase:'applied',transactionId:'a'.repeat(64)}
      return {ok:false,status:500,json:async()=>({detail:'Previous activation could not be confirmed.'})}
    }
    return {ok:true,json:async()=>payload()}
  }))
  render(view())
  fireEvent.click(screen.getByRole('button',{name:'Choose model: Qwen 3.5 4B'}))
  await act(async()=>{})
  fireEvent.click(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/}))
  fireEvent.click(screen.getByRole('button',{name:'Switch model',exact:true}))
  await act(async()=>{await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('alert')).toHaveTextContent('Previous activation could not be confirmed.')
  fireEvent.click(screen.getByRole('button',{name:'Recover model switch'}))
  await act(async()=>{})
  if(succeeds)expect(screen.queryByRole('alert')).toBeNull()
  else expect(screen.getAllByRole('alert').some(node=>node.textContent.includes('Previous activation could not be confirmed.'))).toBe(true)
  expect(posts().map(([url])=>url)).toEqual([`/api/models/${encodeURIComponent(target)}/load`,'/api/models/recovery'])
})

it('prevents a pending confirmation from switching models after a task starts',async()=>{
  const {rerender}=render(view())
  await open()
  fireEvent.click(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/}))
  rerender(view({busy:true}))
  expect(screen.getByRole('button',{name:'Switch model',exact:true})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Switch model',exact:true}))
  expect(posts()).toHaveLength(0)
})

it('routes remote-provider conversations to their settings instead of switching an unrelated local model',async()=>{
  render(view({runtimeSource:'remote-provider',activeModel:'gpt-5.2'}))
  fireEvent.click(screen.getByRole('button',{name:'Choose model: gpt 5.2'}))
  await waitFor(()=>expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toBeDisabled())
  expect(screen.getByRole('link',{name:'Provider settings'})).toHaveAttribute('href','/pixel/settings?section=connections')
  expect(screen.getByRole('menuitemradio',{name:/gpt 5.2/})).toHaveAttribute('aria-checked','true')
  expect(posts()).toHaveLength(0)
})

it.each([undefined,'unrecognized-source'])('blocks local activation until runtime source %s is confirmed',async runtimeSource=>{
  const {rerender}=render(view({runtimeSource,activeModel:'Remote Chat Model'}))
  fireEvent.click(screen.getByRole('button',{name:'Choose model: Remote Chat Model'}))
  const targetOption=await screen.findByRole('menuitemradio',{name:/Qwen 3.5 2B/})
  expect(targetOption).toBeDisabled()
  expect(screen.getByRole('button',{name:'Choose model: Remote Chat Model'})).toBeVisible()
  expect(screen.getByRole('menuitemradio',{name:/Remote Chat Model/})).toHaveAttribute('aria-checked','true')
  expect(screen.getByRole('menuitemradio',{name:/Uncensored/})).toHaveAttribute('aria-checked','false')
  expect(screen.getByRole('link',{name:'Manage models'})).toHaveAttribute('href','/models')
  fireEvent.click(targetOption)
  expect(screen.queryByRole('button',{name:'Switch model',exact:true})).toBeNull()
  expect(posts()).toHaveLength(0)
  rerender(view({runtimeSource:'local-switchboard'}))
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toBeEnabled()
  expect(screen.getByRole('button',{name:'Choose model: Qwen 3.5 4B'})).toBeVisible()
})

it('keeps chat available while another model downloads, while preventing a concurrent swap',async()=>{
  lifecycle={active:true,operation:'model_download',modelId:'another-model'}
  const switching=vi.fn()
  render(view({onSwitchingChange:switching}))
  await open()
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toBeDisabled()
  expect(switching).toHaveBeenLastCalledWith(false)
  expect(switching).not.toHaveBeenCalledWith(true)
  expect(screen.queryByText('Switching…')).toBeNull()
  expect(posts()).toHaveLength(0)
})

it.each([
  [technical,true,'Qwen 3.5 4B'],
  ['Qwen2.5-0.5B-Instruct-GGUF · Q4_K_M',true,'Qwen 2.5 0.5B'],
  ['DeepSeek-R1-Distill-Qwen-7B',true,'DeepSeek R 1 Distill Qwen 7B'],
  ['My Custom Model',false,'My Custom Model'],
])('formats %s without changing the model identifier', (name,compact,expected)=>{
  const model={id:'exact/model-id',name}
  expect(modelDisplayName(model,compact)).toBe(expected)
  expect(model.id).toBe('exact/model-id')
})

it('does not request or poll the model catalog until the selector is first opened',async()=>{
  vi.useFakeTimers()
  const {rerender}=render(view())
  await act(async()=>{await vi.advanceTimersByTimeAsync(60000)})
  expect(fetch).not.toHaveBeenCalled()
  rerender(view({activeModel:'Qwen3.5-2B'}))
  expect(screen.getByRole('button',{name:'Choose model: Qwen 3.5 2B'})).toBeVisible()
  expect(fetch).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Choose model: Qwen 3.5 2B'}))
  await act(async()=>{})
  expect(fetch).toHaveBeenCalledWith('/api/models',expect.any(Object))
  fireEvent.keyDown(window,{key:'Escape'})
  expect(screen.queryByRole('dialog')).toBeNull()
  const reads=fetch.mock.calls.length
  await act(async()=>{await vi.advanceTimersByTimeAsync(30000)})
  expect(fetch.mock.calls.length).toBeGreaterThan(reads)
})

it('reports the active switch instead of an expected temporary unknown source',async()=>{
  lifecycle={active:true,operation:'model_activation',modelId:target}
  render(view({runtimeSource:undefined}))
  await open()
  expect(screen.getByText(/Switching/)).toBeVisible()
  expect(screen.queryByText(/model source is not confirmed/)).toBeNull()
  expect(screen.getByRole('menuitemradio',{name:/Qwen 3.5 2B/})).toHaveAttribute('title','A model operation is in progress.')
  expect(posts()).toHaveLength(0)
})
