import {useState} from 'react'
import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react'
import PortalSubagents from './PortalSubagents'

const first = 'a'.repeat(32), second = 'b'.repeat(32)
const worker = (id,name,status,extra={}) => ({id,name,status,turn:0,task:`${name} assignment`,conversation:[],...extra})
const team = (id,goal,status,agents,extra={}) => ({id,goal,status,agents,...extra})
const waiting = team(first,'Name a cafe','waiting',[
  worker('0','Builder','waiting',{conversation:[{role:'user',content:'Name a cafe\n\nCreate names.'},{role:'assistant',content:'Choose a style.'}],questions:[{id:'style',question:'Which style?',options:['Clean','Playful']}]}),
  worker('1','Reviewer','queued'),
])
function Fixture({teams=[waiting],initial={teamId:first,agentId:'0'},answer=vi.fn(),retry=vi.fn(),stop=vi.fn(),error='',renderApproval}) {
  const [selected,select]=useState(initial)
  return <PortalSubagents controller={{teams,selected,select,answer,retry,stop,error}} renderApproval={renderApproval}/>
}
afterEach(()=>{sessionStorage.clear();vi.unstubAllGlobals()})

it('groups actual agents from all teams, including goal runs, and opens the correct conversation without a modal',()=>{
  const teams=[team(first,'First task','running',[worker('0','Builder','running'),worker('1','Reviewer','failed'),worker('2','Explorer','interrupted')],{planning:{id:'0',name:'Planning ghost',status:'running'}}),team(second,'Second task','completed',[worker('0','Reporter','completed',{conversation:[{role:'assistant',content:'Second result.'}]})],{mode:'goal'})]
  render(<Fixture teams={teams} initial={null}/>)
  expect(within(screen.getByRole('region',{name:'Active'})).getAllByRole('button')).toHaveLength(1)
  const attention=screen.getByRole('region',{name:'Needs attention'})
  expect(within(attention).getByRole('button',{name:'Reviewer · Needs attention'})).toBeVisible()
  expect(within(attention).getByRole('button',{name:'Explorer · Unconfirmed'})).toBeVisible()
  const completed=screen.getByRole('region',{name:'Completed'})
  expect(within(completed).queryByText('Reviewer')).toBeNull()
  expect(screen.queryByText('Planning ghost')).toBeNull()
  fireEvent.click(within(completed).getByRole('button',{name:'Reporter · Completed'}))
  expect(screen.getByRole('region',{name:'Reporter conversation'})).toHaveTextContent('Second result.')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button',{name:'Back to subagents'})).toHaveFocus()
  fireEvent.click(screen.getByRole('button',{name:'Back to subagents'}))
  expect(screen.getByRole('region',{name:'Active'})).toBeVisible()
  expect(screen.getByRole('region',{name:'Completed'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Reporter · Completed'})).toHaveFocus()
})

it('defers conversation focus while hidden and does not steal it on ordinary polling',()=>{
  const selection={teamId:first,agentId:'0'}
  const view=render(<div hidden><Fixture initial={selection}/></div>)
  expect(screen.getByRole('button',{name:'Back to subagents',hidden:true})).not.toHaveFocus()
  view.rerender(<div><Fixture initial={selection}/></div>)
  expect(screen.getByRole('button',{name:'Back to subagents'})).toHaveFocus()
  screen.getByRole('button',{name:'Stop team'}).focus()
  view.rerender(<div><Fixture initial={selection}/></div>)
  expect(screen.getByRole('button',{name:'Stop team'})).toHaveFocus()
})

it('retains question drafts across navigation and remount, scopes them by turn, and submits only to the selected agent',async()=>{
  let resolveAnswer
  const answer=vi.fn(()=>new Promise(resolve=>{resolveAnswer=resolve}))
  let view=render(<Fixture answer={answer}/>)
  fireEvent.click(screen.getByRole('radio',{name:/Playful/}))
  fireEvent.click(screen.getByRole('button',{name:'Back to subagents'}))
  fireEvent.click(screen.getByRole('button',{name:'Reviewer · Queued'}))
  expect(screen.getByText('Waiting for its turn; it has not started.')).toBeVisible()
  expect(screen.queryByText('Choose a style.')).toBeNull()
  view.unmount()
  view=render(<Fixture answer={answer}/>)
  expect(screen.getByRole('radio',{name:/Playful/})).toBeChecked()
  fireEvent.click(screen.getByRole('button',{name:'Continue'}))
  fireEvent.click(screen.getByRole('button',{name:'Continue'}))
  expect(answer).toHaveBeenCalledExactlyOnceWith(first,'0',{style:'Playful'})
  expect(screen.getByRole('button',{name:'Stop team'})).toBeDisabled()
  await act(async()=>resolveAnswer())
  expect(screen.getByRole('radio',{name:/Playful/})).toBeChecked()
  expect(JSON.parse(sessionStorage.getItem(`portal-team-answer:${first}:0:0`))).toEqual({style:'Playful'})
  view.rerender(<Fixture teams={[{...waiting,agents:[{...waiting.agents[0],turn:1},waiting.agents[1]]}]} answer={answer}/>)
  expect(screen.getByRole('radio',{name:/Playful/})).not.toBeChecked()
})

it('preserves a draft when the controller reports a failed answer and allows another attempt',async()=>{
  const answer=vi.fn().mockResolvedValue(undefined)
  const view=render(<Fixture answer={answer}/>)
  fireEvent.click(screen.getByRole('radio',{name:/Clean/}))
  fireEvent.click(screen.getByRole('button',{name:'Continue'}))
  await waitFor(()=>expect(screen.getByRole('button',{name:'Continue'})).toBeEnabled())
  view.rerender(<Fixture answer={answer} error="The runtime is unavailable."/>)
  expect(screen.getByRole('alert')).toHaveTextContent('The runtime is unavailable.')
  expect(screen.getByRole('radio',{name:/Clean/})).toBeChecked()
  fireEvent.click(screen.getByRole('button',{name:'Continue'}))
  await waitFor(()=>expect(answer).toHaveBeenCalledTimes(2))
})

it('routes retry and stop to their real team and does not let a late action error leak into another agent',async()=>{
  let rejectRetry
  const retry=vi.fn(()=>new Promise((resolve,reject)=>{rejectRetry=reject})), stop=vi.fn().mockResolvedValue(undefined)
  const teams=[team(first,'Fix it','failed',[worker('0','Reviewer','failed',{retryable:true,error:'Runtime disconnected.'}),worker('1','Verifier','completed')]),team(second,'Goal','stopping',[worker('0','Coordinator','interrupted')],{mode:'goal'})]
  render(<Fixture teams={teams} retry={retry} stop={stop}/>)
  fireEvent.click(screen.getByRole('button',{name:'Retry agent'}))
  expect(retry).toHaveBeenCalledExactlyOnceWith(first,'0')
  fireEvent.click(screen.getByRole('button',{name:'Back to subagents'}))
  fireEvent.click(screen.getByRole('button',{name:'Coordinator · Unconfirmed'}))
  expect(screen.getByRole('button',{name:'Confirm stop'})).toBeDisabled()
  await act(async()=>rejectRetry(new Error('Retry rejected.')))
  expect(screen.queryByText('Retry rejected.')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Confirm stop'}))
  await waitFor(()=>expect(stop).toHaveBeenCalledExactlyOnceWith(second))
})

it('renders safe Markdown, copies exact response text, places approvals with their response, and avoids duplicate live output',async()=>{
  const content='**Result** with [source](https://example.com/proof).\n\n[unsafe](javascript:alert(1))\n\n![remote image](https://example.com/track.png)\n\n<script>alert(1)</script>'
  const writeText=vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator',{clipboard:{writeText}})
  const teams=[team(first,'One assignment','completed',[worker('0','Builder','completed',{conversation:[{role:'user',content:'One assignment'},{role:'assistant',content}],output:content})])]
  const renderApproval=vi.fn(()=> <button>Review requested action</button>)
  const {container}=render(<Fixture teams={teams} renderApproval={renderApproval}/>)
  expect(screen.getAllByText('One assignment')).toHaveLength(1)
  expect(screen.getAllByText('Result')).toHaveLength(1)
  expect(screen.getByRole('link')).toHaveAttribute('href','https://example.com/proof')
  expect(container.querySelectorAll('img')).toHaveLength(1)
  expect(new URL(container.querySelector('img').src).searchParams.get('url')).toBe('https://example.com')
  expect(container.querySelector('img[src="https://example.com/track.png"]')).toBeNull()
  expect(container.querySelector('script')).toBeNull()
  expect(screen.queryByText(/Qwen|GPT|Model:/)).toBeNull()
  expect(renderApproval).toHaveBeenCalledWith(content)
  fireEvent.click(screen.getByRole('button',{name:'Copy response'}))
  await waitFor(()=>expect(writeText).toHaveBeenCalledExactlyOnceWith(content))
  expect(screen.getByRole('button',{name:'Review requested action'})).toBeVisible()
})

it('shows available recorded progress and waits honestly when the runtime has not started',()=>{
  const timestamp='2026-09-15T10:00:00.000Z'
  const activity={schemaVersion:2,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:timestamp,finishedAt:null,state:'running',calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'read',calls:1,failures:0,blocked:0}],events:[{sequence:1,kind:'read',state:'completed',startedAt:timestamp,finishedAt:timestamp}],context:null,goal:null}
  const view=render(<Fixture teams={[team(first,'Inspect files','running',[worker('0','Explorer','running',{activity})])]}/> )
  expect(screen.getByRole('list',{name:'Execution steps'})).toHaveTextContent('Reading files')
  view.rerender(<Fixture teams={[team(first,'Inspect files','running',[worker('0','Explorer','running',{runtime_wait:true})])]}/> )
  expect(screen.getByText('Waiting for the model runtime to become ready. No new work has been sent.')).toBeVisible()
  expect(screen.queryByRole('list',{name:'Execution steps'})).toBeNull()
})

it('handles removed selections, empty teams and unavailable storage without losing usable controls',()=>{
  const view=render(<Fixture teams={[]}/>)
  expect(screen.getByText('Subagents will appear here when a team starts.')).toBeVisible()
  vi.spyOn(Storage.prototype,'getItem').mockImplementation(()=>{throw new Error('Blocked')})
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('Blocked')})
  view.rerender(<Fixture/>)
  fireEvent.click(screen.getByRole('radio',{name:/Clean/}))
  expect(screen.getByRole('radio',{name:/Clean/})).toBeChecked()
  expect(screen.getByRole('button',{name:'Continue'})).toBeEnabled()
  vi.restoreAllMocks()
})
