import {render,screen,fireEvent,waitFor} from '@testing-library/react'
import PortalApprovalTerminal from './PortalApprovalTerminal'
const job='ops-1790800000000-'+'a'.repeat(12),plan='b'.repeat(64),session='c'.repeat(64)
let requests
beforeEach(()=>{
  requests=[]
  vi.stubGlobal('fetch',vi.fn(async(_url,options)=>{
    const body=JSON.parse(options.body);requests.push(body)
    const value=body.action==='start'?{session,state:'running',nextSequence:0}:body.action==='poll'?{state:'running',output:'Protected plan\nPassword:',exitCode:null,cleanupConfirmed:false,nextCursor:25,moreOutput:false}:body.action==='input'?{accepted:true,nextSequence:1}:{stopped:true,cleanupConfirmed:true}
    return {ok:true,json:async()=>value}
  }))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})

test('requires explicit user open and sends only fixed identity, never an approval boolean',async()=>{
  render(<PortalApprovalTerminal job={job} plan={plan}/>)
  expect(fetch).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Open approval terminal'}))
  await screen.findByRole('log')
  expect(requests[0]).toEqual({action:'start',job,plan})
  expect(await screen.findByText(/Protected plan/)).toBeVisible()
  const field=screen.getByLabelText('Private terminal input')
  expect(field).toHaveAttribute('type','password')
  fireEvent.change(field,{target:{value:'private-password'}})
  fireEvent.click(screen.getByRole('button',{name:'Send input'}))
  await waitFor(()=>expect(requests.find(item=>item.action==='input')).toEqual({action:'input',session,sequence:0,line:'private-password'}))
  expect(field).toHaveValue('')
  expect(screen.getByRole('log')).not.toHaveTextContent('private-password')
  expect(JSON.stringify(localStorage)).not.toContain('private-password')
})

test('preserves a diagnostic rather than claiming approval when input acknowledgement is lost',async()=>{
  render(<PortalApprovalTerminal job={job} plan={plan}/>)
  fireEvent.click(screen.getByRole('button',{name:'Open approval terminal'}))
  await screen.findByRole('log')
  fetch.mockImplementationOnce(async()=>{throw new Error('Network unavailable')})
  fireEvent.change(screen.getByLabelText('Private terminal input'),{target:{value:'secret'}})
  fireEvent.click(screen.getByRole('button',{name:'Send input'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable')
  expect(screen.queryByRole('button',{name:'Send input'})).toBeNull()
  expect(screen.getByRole('button',{name:'Stop terminal'})).toBeVisible()
})

test('unmount cancels only its exact private session',async()=>{
  const view=render(<PortalApprovalTerminal job={job} plan={plan}/>)
  fireEvent.click(screen.getByRole('button',{name:'Open approval terminal'}))
  await screen.findByRole('log');view.unmount()
  await waitFor(()=>expect(requests.at(-1)).toEqual({action:'cancel',session}))
})

test('reopens only after exact stop and credential cleanup acknowledgement',async()=>{
  render(<PortalApprovalTerminal job={job} plan={plan}/>)
  fireEvent.click(screen.getByRole('button',{name:'Open approval terminal'}));await screen.findByRole('log')
  expect(screen.getByText('Stopping this terminal does not cancel an already approved operation.')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Stop terminal'}))
  fireEvent.click(await screen.findByRole('button',{name:'Open new approval terminal'}))
  await waitFor(()=>expect(requests.filter(value=>value.action==='start')).toHaveLength(2))
})

test('unconfirmed cleanup cannot reopen another approval session',async()=>{
  render(<PortalApprovalTerminal job={job} plan={plan}/>)
  fireEvent.click(screen.getByRole('button',{name:'Open approval terminal'}));await screen.findByRole('log')
  fetch.mockImplementationOnce(async()=>({ok:true,json:async()=>({stopped:true,cleanupConfirmed:false})}))
  fireEvent.click(screen.getByRole('button',{name:'Stop terminal'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('cleanup was not confirmed')
  expect(screen.queryByRole('button',{name:'Open new approval terminal'})).toBeNull()
})

test('a new job/plan component key cancels old custody and requires a fresh explicit start',async()=>{
  const view=render(<PortalApprovalTerminal key={`${job}:${plan}`} job={job} plan={plan}/>)
  fireEvent.click(screen.getByRole('button',{name:'Open approval terminal'}));await screen.findByRole('log')
  const newPlan='d'.repeat(64)
  view.rerender(<PortalApprovalTerminal key={`${job}:${newPlan}`} job={job} plan={newPlan}/>)
  expect(screen.getByRole('button',{name:'Open approval terminal'})).toBeVisible()
  await waitFor(()=>expect(requests.at(-1)).toEqual({action:'cancel',session}))
  expect(requests.filter(value=>value.action==='start')).toHaveLength(1)
})
