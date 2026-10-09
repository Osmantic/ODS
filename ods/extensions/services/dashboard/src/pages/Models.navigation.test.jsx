import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Models from './Models'

// Keep the production inventory/activation hook and page together. Downloads
// are a separate transport and are not involved in these navigation cases.
vi.mock('../hooks/useDownloadProgress', () => ({
  useDownloadProgress: () => ({ isDownloading: false, refresh: vi.fn() }),
}))

const target = 'qwen3.5-9b-q4'
const model = {
  id: target, name: 'Qwen 3.5 9B', status: 'downloaded',
  size: '5.6 GB', sizeGb: 5.6, vramRequired: 7, fitsVram: true,
  contextLength: 65536, maxContextLength: 65536, specialty: 'General',
  quantization: 'Q4_K_M', publisher: { name: 'Qwen' },
}
const response = (active = false) => ({ ok: true, json: async () => ({
  models: [model], gpu: { vramTotal: 16, vramFree: 16, vramUsed: 0 },
  odsMode: 'local', configuredMode: 'local', llmBackend: 'llama-server',
  modelLifecycle: active ? { active: true, operation: 'model_activation', modelId: target } : null,
}) })
const reads = () => fetch.mock.calls.filter(([url]) => url === '/api/models')
const posts = () => fetch.mock.calls.filter(([, options]) => options?.method === 'POST')

async function openModels() {
  const view = render(<StrictMode><MemoryRouter><Models compact /></MemoryRouter></StrictMode>)
  await act(async () => {})
  return view
}

async function startActivation() {
  fireEvent.click(screen.getByRole('button', { name: 'Run' }))
  fireEvent.click(screen.getByRole('button', { name: 'Run model' }))
  await act(async () => {})
  expect(posts()).toHaveLength(1)
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    if (options?.method === 'POST') return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new window.DOMException('Aborted', 'AbortError')), { once: true })
    })
    return Promise.resolve(response())
  }))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete document.hidden
})

it('shows actual rollback progress after navigation while blocking a new model load',async()=>{
  vi.stubGlobal('fetch',vi.fn(async url=>{
    if(url==='/api/models/recovery')return {ok:true,json:async()=>({pending:true,phase:'held',transactionId:'a'.repeat(64)})}
    const payload=await response(true).json()
    return {ok:true,json:async()=>({...payload,modelActivation:{active:true,phase:'rolling_back',failureCode:'runtime_load_failed'}})}
  }))
  await openModels()
  expect(screen.getByRole('alert',{name:'Model activation'})).toHaveTextContent('restoring previous model')
  expect(screen.getByRole('button',{name:'Working'})).toBeDisabled()
  expect(screen.queryByRole('button',{name:'Recover model switch'})).toBeNull()
  expect(posts()).toHaveLength(0)
})

it('offers the existing guarded recovery only after an authoritative pending read',async()=>{
  vi.stubGlobal('fetch',vi.fn(async url=>{
    if(url==='/api/models/recovery')return {ok:true,json:async()=>({pending:true,phase:'held',transactionId:'a'.repeat(64)})}
    const payload=await response(false).json()
    return {ok:true,json:async()=>({...payload,modelRecoveryPending:true,
      modelActivation:{active:false,outcome:'rollback_unconfirmed',failureCode:'rollback_unconfirmed'}})}
  }))
  await openModels()
  expect(screen.getByRole('alert',{name:'Model activation'})).toHaveTextContent('Model recovery needs attention')
  expect(screen.getByRole('button',{name:'Recover model switch'})).toBeEnabled()
  expect(screen.getByRole('button',{name:'Run'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Run'}))
  expect(screen.queryByRole('button',{name:'Run model'})).toBeNull()
  expect(posts()).toHaveLength(0)
})

it('blocks an open activation dialog while recovery is pending and unlocks only after recovery readback',async()=>{
  let pending=false
  vi.stubGlobal('fetch',vi.fn(async (url,options)=>{
    if(url==='/api/models/recovery'){
      if(options?.method==='POST')pending=false
      return {ok:true,json:async()=>({pending,phase:pending?'held':'completed',transactionId:'a'.repeat(64)})}
    }
    const payload=await response(false).json()
    return {ok:true,json:async()=>({...payload,modelRecoveryPending:pending})}
  }))
  await openModels()
  fireEvent.click(screen.getByRole('button',{name:'Run'}))
  expect(screen.getByRole('button',{name:'Run model'})).toBeEnabled()
  pending=true
  fireEvent.click(screen.getByTitle('Refresh models'))
  await act(async()=>{})
  expect(screen.getByRole('button',{name:'Run model'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Run model'}))
  expect(posts()).toHaveLength(0)
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}))
  fireEvent.click(screen.getByRole('button',{name:'Recover model switch'}))
  await act(async()=>{})
  expect(posts().map(([url])=>url)).toEqual(['/api/models/recovery'])
  expect(screen.getByRole('button',{name:'Run'})).toBeEnabled()
  expect(screen.queryByRole('button',{name:'Recover model switch'})).toBeNull()
})

it('stops all activation polling after leaving Models between polls', async () => {
  const view = await openModels()
  await startActivation()
  view.unmount()
  const atNavigation = reads().length
  await act(async () => { await vi.advanceTimersByTimeAsync(620000) })
  expect(reads()).toHaveLength(atNavigation)
  expect(posts()[0][1].signal.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})

it.each(['headers', 'body'])('does not resume after the active status %s arrive late', async stage => {
  const view = await openModels()
  await startActivation()
  let release
  const late = new Promise(resolve => { release = resolve })
  fetch.mockImplementation((url, options) => {
    if (options?.method === 'POST') throw new Error('Unexpected second activation')
    return stage === 'headers' ? late : Promise.resolve({ ok: true, json: () => late })
  })
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  const activeReads = reads().slice(1)
  view.unmount()
  const atNavigation = reads().length
  await act(async () => { release(stage === 'headers' ? response() : await response().json()) })
  await act(async () => { await vi.advanceTimersByTimeAsync(620000) })
  expect(reads()).toHaveLength(atNavigation)
  // At least the activation-owned read, in addition to the background poll,
  // is cancelled. Late delivery cannot start its next or final status read.
  expect(activeReads.some(([, options]) => options.signal.aborted)).toBe(true)
})

it('reads backend activation on return without replaying the POST', async () => {
  const first = await openModels()
  await startActivation()
  first.unmount()
  fetch.mockImplementation(() => Promise.resolve(response(true)))
  const second = await openModels()
  expect(screen.getByRole('button', { name: 'Working' })).toBeDisabled()
  expect(posts()).toHaveLength(1)
  second.unmount()
  const atNavigation = reads().length
  await act(async () => { await vi.advanceTimersByTimeAsync(620000) })
  expect(reads()).toHaveLength(atNavigation)
})
