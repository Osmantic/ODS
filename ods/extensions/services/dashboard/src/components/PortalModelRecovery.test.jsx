import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import PortalModelRecovery from './PortalModelRecovery'

const pending={pending:true,phase:'applied',transactionId:'a'.repeat(64)}
const done={...pending,pending:false,phase:'completed',outcome:'commit'}
const reply=(body,ok=true)=>({ok,json:async()=>body})
afterEach(()=>vi.unstubAllGlobals())

it('is invisible for an idle or unsupported runtime and never starts recovery automatically',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>reply({pending:false,phase:'idle',transactionId:null})))
  const {container}=render(<PortalModelRecovery/>)
  await waitFor(()=>expect(fetch).toHaveBeenCalledOnce())
  expect(container).toBeEmptyDOMElement()
  expect(fetch.mock.calls[0][1].method).toBeUndefined()
})

it('recovers only the existing transaction and prevents duplicate clicks',async()=>{
  let resolvePost
  vi.stubGlobal('fetch',vi.fn(async(_,options)=>options?.method==='POST'?await new Promise(resolve=>{resolvePost=resolve}):reply(pending)))
  const recovered=vi.fn(),busy=vi.fn(),changed=vi.fn()
  render(<PortalModelRecovery onRecovered={recovered} onBusyChange={busy} onPendingChange={changed}/>)
  const button=await screen.findByRole('button',{name:'Recover model switch'})
  fireEvent.click(button);fireEvent.click(button)
  expect(fetch.mock.calls.filter(([,options])=>options.method==='POST')).toHaveLength(1)
  expect(fetch.mock.calls[1][1]).toMatchObject({method:'POST',body:'{}'})
  expect(busy).toHaveBeenLastCalledWith(true)
  await act(async()=>resolvePost(reply(done)))
  expect(recovered).toHaveBeenCalledOnce()
  expect(changed).toHaveBeenLastCalledWith(false)
  expect(screen.queryByRole('button')).toBeNull()
})

it('keeps pending work and explains missing proof without claiming success',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(_,options)=>reply(options?.method==='POST'?{...pending,reason:'model-recovery-proof-required'}:pending,options?.method!=='POST')))
  const recovered=vi.fn()
  render(<PortalModelRecovery onRecovered={recovered}/>)
  fireEvent.click(await screen.findByRole('button',{name:'Recover model switch'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('still needs repair')
  expect(recovered).not.toHaveBeenCalled()
  expect(screen.getByRole('button',{name:'Recover model switch'})).toBeEnabled()
})

it('a lost response does not replay the mutation or mark it complete',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(_,options)=>{if(options?.method==='POST')throw new Error('lost');return reply(pending)}))
  const recovered=vi.fn()
  render(<PortalModelRecovery onRecovered={recovered}/>)
  fireEvent.click(await screen.findByRole('button',{name:'Recover model switch'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be confirmed')
  expect(fetch.mock.calls.filter(([,options])=>options.method==='POST')).toHaveLength(1)
  expect(recovered).not.toHaveBeenCalled()
})

it.each([
  [409,pending],
  [503,{pending:true,phase:'unavailable',transactionId:null}],
])('shows a validated pending receipt returned with HTTP %s',async(status,state)=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({...reply(state,false),status})))
  const changed=vi.fn(),recovered=vi.fn()
  render(<PortalModelRecovery onPendingChange={changed} onRecovered={recovered}/>)
  expect(await screen.findByRole('button',{name:'Recover model switch'})).toBeEnabled()
  expect(changed).toHaveBeenLastCalledWith(true)
  expect(recovered).not.toHaveBeenCalled()
  expect(fetch.mock.calls.every(([,options])=>!options.method)).toBe(true)
})

it.each([
  [404,pending],
  [500,pending],
  [503,{detail:'unavailable'}],
  [409,{pending:false,phase:'held',transactionId:'a'.repeat(64)}],
])('does not infer recovery state from an unrelated or invalid HTTP %s response',async(status,state)=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({...reply(state,false),status})))
  const changed=vi.fn(),recovered=vi.fn()
  const view=render(<PortalModelRecovery onPendingChange={changed} onRecovered={recovered}/>)
  await act(async()=>{})
  expect(view.container).toBeEmptyDOMElement()
  expect(changed).not.toHaveBeenCalled()
  expect(recovered).not.toHaveBeenCalled()
})

it('closing the menu does not abort accepted recovery; reopening reads persisted state',async()=>{
  let resolvePost,signal
  vi.stubGlobal('fetch',vi.fn(async(_,options)=>{
    if(options?.method==='POST'){signal=options.signal;return await new Promise(resolve=>{resolvePost=resolve})}
    return reply(pending)
  }))
  const recovered=vi.fn()
  const view=render(<PortalModelRecovery active onRecovered={recovered}/>)
  fireEvent.click(await screen.findByRole('button',{name:'Recover model switch'}))
  view.rerender(<PortalModelRecovery active={false} onRecovered={recovered}/>)
  expect(signal.aborted).toBe(false)
  await act(async()=>resolvePost(reply(done)))
  expect(recovered).toHaveBeenCalledOnce()
})
