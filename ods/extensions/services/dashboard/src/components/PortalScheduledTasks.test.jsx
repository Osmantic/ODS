import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {webcrypto} from 'node:crypto'
import PortalScheduledTasks from './PortalScheduledTasks'

const job=(id='task-a',name='Morning agenda')=>({id,name,task:'Summarize my upcoming day.',revision:'a'.repeat(64),enabled:true,
  schedule:{kind:'cron',expr:'0 9 * * *',tz:'UTC'},createdAt:1,updatedAt:1,nextRunAt:1900000000000,
  lastRunAt:null,runningSince:null,lastStatus:null,externalDelivery:false,removed:false,removalPending:false})
const page=items=>({kind:'tasks',items,offset:0,hasMore:false,nextOffset:null,pendingCreates:[],pendingCount:0,pendingRemovals:[]})
const change=task=>({kind:'change',outcome:'applied',task})
const runs=id=>({kind:'runs',taskId:id,offset:0,hasMore:false,nextOffset:null,
  items:[{timestamp:1800000000000,status:'ok',summary:'One synthetic meeting at 09:00.',summaryTruncated:false,durationMs:30}]})
const reply=(body,status=200)=>new globalThis.Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
let jobs,archived,calls
beforeEach(()=>{
  vi.stubGlobal('crypto',webcrypto)
  jobs=[];archived=[];calls=[]
  vi.stubGlobal('fetch',vi.fn(async(_url,options)=>{
    const body=JSON.parse(options.body);calls.push(body)
    if(body.action==='list')return reply(page(jobs))
    if(body.action==='archived')return reply(page(archived))
    if(body.action==='create') {const value={...job(),name:body.name,task:body.task};jobs.push(value);return reply(change(value))}
    if(body.action==='runs')return reply(runs(body.id))
    if(body.action==='set-enabled') {jobs=jobs.map(item=>({...item,enabled:body.enabled}));return reply(change(jobs[0]))}
    if(body.action==='remove') {const item={...jobs[0],removed:true,enabled:false};archived.push(item);jobs=[];return reply(change(item))}
    throw new Error('Unexpected test operation')
  }))
})
afterEach(()=>vi.unstubAllGlobals())

async function fillForm() {
  fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}))
  fireEvent.change(screen.getByLabelText('Name'),{target:{value:'Morning agenda'}})
  fireEvent.change(screen.getByLabelText('What should Portal do?'),{target:{value:'Summarize my upcoming day.'}})
}

it('uses the native task flow: create, read result, pause, remove, return to saved result',async()=>{
  render(<PortalScheduledTasks/>)
  expect(await screen.findByText('No scheduled tasks yet')).toBeVisible()
  await fillForm()
  expect(screen.getByLabelText('Name')).toHaveFocus()
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  fireEvent.click(await screen.findByRole('button',{name:/Morning agenda.*Daily/}))
  expect(await screen.findByText('One synthetic meeting at 09:00.')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Pause'}))
  fireEvent.click(await screen.findByRole('button',{name:/Morning agenda.*Paused/}))
  await screen.findByText('One synthetic meeting at 09:00.')
  fireEvent.click(screen.getByRole('button',{name:'Remove schedule'}))
  expect(screen.getByText(/A run already underway may finish/)).toBeVisible()
  fireEvent.click(screen.getAllByRole('button',{name:'Remove schedule'}).at(-1))
  await screen.findByText('No scheduled tasks yet')
  fireEvent.click(screen.getByRole('button',{name:'Past tasks'}))
  fireEvent.click(await screen.findByRole('button',{name:/Morning agenda.*Removed/}))
  expect(await screen.findByText('One synthetic meeting at 09:00.')).toBeVisible()
  const created=calls.find(item=>item.action==='create')
  expect(created.requestId).toMatch(/^[a-f0-9-]{36}$/)
  expect(created.schedule).toEqual({kind:'recurring',frequency:'daily',time:'09:00',timeZone:expect.any(String),weekday:null})
  expect(calls.find(item=>item.action==='set-enabled')).toEqual({action:'set-enabled',id:'task-a',revision:'a'.repeat(64),enabled:false})
  expect(calls.filter(item=>item.action==='create')).toHaveLength(1)
})

it('checks a lost creation reply with the same identity and never resends the add',async()=>{
  const normal=fetch.getMockImplementation()
  fetch.mockImplementation(async(url,options)=>{
    const body=JSON.parse(options.body)
    if(body.action==='create') {calls.push(body);jobs.push(job());throw new TypeError('Connection lost after accept')}
    if(body.action==='check-create') {calls.push(body);return reply({kind:'change',outcome:'unchanged',task:jobs[0]})}
    return normal(url,options)
  })
  render(<PortalScheduledTasks/>)
  await fillForm()
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  const check=await screen.findByRole('button',{name:'Check request'})
  expect(screen.getByRole('button',{name:'Schedule task'})).toBeDisabled()
  fireEvent.click(check)
  await screen.findByRole('button',{name:/Morning agenda.*Daily/})
  expect(calls.filter(item=>item.action==='create')).toHaveLength(1)
  expect(calls.find(item=>item.action==='check-create').requestId).toBe(calls.find(item=>item.action==='create').requestId)
})

it('leaves refused creation editable rather than inventing an unknown task',async()=>{
  const normal=fetch.getMockImplementation()
  fetch.mockImplementation(async(url,options)=>JSON.parse(options.body).action==='create'
    ?reply({detail:{code:'controller-task-time-passed'}},409):normal(url,options))
  render(<PortalScheduledTasks/>)
  await fillForm()
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  expect(await screen.findByText('Choose a time in the future.')).toBeVisible()
  expect(screen.queryByRole('button',{name:'Check request'})).not.toBeInTheDocument()
  expect(screen.getByRole('button',{name:'Schedule task'})).toBeEnabled()
})

it.each(['network','invalid-reply','transition-busy'])('can explicitly refresh a failed task-list read without suggesting an uncertain change (%s)',async failure=>{
  const normal=fetch.getMockImplementation()
  fetch.mockImplementation(async(url,options)=>{
    calls.push(JSON.parse(options.body))
    if(failure==='network')throw new TypeError('Connection lost during list')
    return failure==='transition-busy'?reply({detail:{code:'controller-transition-busy'}},409):reply({kind:'tasks'})
  })
  render(<PortalScheduledTasks/>)
  expect(await screen.findByRole('alert')).toHaveTextContent(failure==='transition-busy'
    ?'ODS is applying another change. This request did not start. Try again in a moment.'
    :'ODS could not load scheduled tasks. Refresh to try again.')
  expect(calls).toEqual([{action:'list',offset:0}])
  expect(screen.queryByRole('button',{name:'Check request'})).not.toBeInTheDocument()
  fetch.mockImplementation(normal)
  fireEvent.click(screen.getByRole('button',{name:'Refresh scheduled tasks'}))
  expect(await screen.findByText('No scheduled tasks yet')).toBeVisible()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(calls).toEqual([{action:'list',offset:0},{action:'list',offset:0}])
})

it('reports a result-read failure without implying a task change and keeps explicit refresh read-only',async()=>{
  jobs=[job()]
  const normal=fetch.getMockImplementation()
  render(<PortalScheduledTasks/>)
  const row=await screen.findByRole('button',{name:/Morning agenda.*Daily/})
  fetch.mockImplementation(async(url,options)=>{
    const body=JSON.parse(options.body)
    if(body.action==='runs') {calls.push(body);throw new TypeError('Connection lost during result read')}
    return normal(url,options)
  })
  fireEvent.click(row)
  expect(await screen.findByRole('alert')).toHaveTextContent('ODS could not load saved results. Refresh to try again.')
  fetch.mockImplementation(normal)
  fireEvent.click(screen.getByRole('button',{name:'Refresh scheduled tasks'}))
  expect(await screen.findByText('One synthetic meeting at 09:00.')).toBeVisible()
  expect(calls.map(item=>item.action)).toEqual(['list','runs','runs'])
})

it.each(['transition-busy','controller-transition-busy'])('keeps a creation refused by the controller lock editable and requires a new explicit submission (%s)',async code=>{
  const normal=fetch.getMockImplementation()
  fetch.mockImplementation(async(url,options)=>{
    const body=JSON.parse(options.body)
    if(body.action==='create') {calls.push(body);return reply({detail:{code}},409)}
    return normal(url,options)
  })
  render(<PortalScheduledTasks/>)
  await fillForm()
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('This request did not start.')
  expect(screen.queryByRole('button',{name:'Check request'})).not.toBeInTheDocument()
  expect(screen.getByLabelText('Name')).toHaveValue('Morning agenda')
  expect(screen.getByRole('button',{name:'Schedule task'})).toBeEnabled()
  fetch.mockImplementation(normal)
  expect(calls.filter(item=>item.action==='create')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  expect(await screen.findByRole('button',{name:/Morning agenda.*Daily/})).toBeVisible()
  expect(calls.filter(item=>item.action==='create')).toHaveLength(2)
  expect(jobs).toHaveLength(1)
})

it('clears an old result during refresh and ignores a reply after unmount',async()=>{
  jobs=[job()]
  const {unmount}=render(<PortalScheduledTasks/>)
  fireEvent.click(await screen.findByRole('button',{name:/Morning agenda.*Daily/}))
  await screen.findByText('One synthetic meeting at 09:00.')
  let complete
  fetch.mockImplementation(()=>new Promise(resolve=>{complete=resolve}))
  fireEvent.click(screen.getByRole('button',{name:'Refresh scheduled tasks'}))
  expect(screen.queryByText('One synthetic meeting at 09:00.')).not.toBeInTheDocument()
  unmount()
  await act(async()=>complete(reply(runs('task-a'))))
  expect(screen.queryByLabelText('Scheduled tasks')).not.toBeInTheDocument()
})

it('treats summaries as text and reports shortened results',async()=>{
  jobs=[job()]
  const normal=fetch.getMockImplementation()
  fetch.mockImplementation(async(url,options)=>{
    const request=JSON.parse(options.body)
    if(request.action==='runs') {const body=runs(request.id);body.items[0].summary='<img src="https://example.invalid/private">';body.items[0].summaryTruncated=true;return reply(body)}
    return normal(url,options)
  })
  render(<PortalScheduledTasks/>)
  fireEvent.click(await screen.findByRole('button',{name:/Morning agenda.*Daily/}))
  expect(await screen.findByText('<img src="https://example.invalid/private">')).toBeVisible()
  expect(document.querySelector('img')).toBeNull()
  expect(screen.getByText('This summary was shortened for display.')).toBeVisible()
})

it('does not send instructions that exceed the runtime byte limit',async()=>{
  render(<PortalScheduledTasks/>)
  await fillForm()
  fireEvent.change(screen.getByLabelText('What should Portal do?'),{target:{value:'🦊'.repeat(1100)}})
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('Use a shorter name or instructions'))
  expect(calls.filter(item=>item.action==='create')).toHaveLength(0)
})

it('can schedule over private-LAN HTTP without crypto.randomUUID',async()=>{
  vi.stubGlobal('crypto',{getRandomValues:values=>webcrypto.getRandomValues(values)})
  render(<PortalScheduledTasks/>)
  await fillForm()
  fireEvent.click(screen.getByRole('button',{name:'Schedule task'}))
  await screen.findByRole('button',{name:/Morning agenda.*Daily/})
  expect(calls.find(item=>item.action==='create').requestId).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
})
