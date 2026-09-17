import {act, fireEvent, render, screen, within} from '@testing-library/react'
import PortalGoalPlan from './PortalGoalPlan'

const time='2026-09-15T10:00:00.000Z'
const task={schemaVersion:3,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:time,finishedAt:null,state:'running',calls:0,failures:0,blocked:0,truncated:false,activities:[],events:[],context:null,goal:null}
const steps=[{id:'read',title:'Read the project',status:'completed'},{id:'build',title:'Build the page',status:'running'},{id:'verify',title:'Verify the result',status:'pending'}]
const planned={...task,goal:{summary:'Improve the project',status:'active',steps}}
afterEach(()=>vi.useRealTimers())

it('renders real reported states without simulating advancement or offering checkboxes',async()=>{
  vi.useFakeTimers()
  render(<PortalGoalPlan task={planned} active/>)
  expect(screen.getByRole('status')).toHaveTextContent('1/3')
  const rows=within(screen.getByRole('list',{name:'Reported tasks'})).getAllByRole('listitem')
  expect(rows.map(row=>row.dataset.state)).toEqual(['completed','running','pending'])
  expect(rows[1]).toHaveTextContent('In progress')
  expect(screen.queryByRole('checkbox')).toBeNull()
  await act(async()=>vi.advanceTimersByTimeAsync(60_000))
  expect(screen.getByRole('status')).toHaveTextContent('1/3')
  expect(rows[2]).toHaveAttribute('data-state','pending')
})

it('collapses tasks while retaining the summary and user choice through progress updates',()=>{
  const {rerender}=render(<PortalGoalPlan task={planned} active/>)
  const trigger=screen.getByRole('button',{name:'Toggle tasks'})
  fireEvent.click(trigger)
  expect(trigger).toHaveAttribute('aria-expanded','false')
  expect(screen.queryByRole('list')).toBeNull()
  expect(screen.getByText('Improve the project')).toBeVisible()
  rerender(<PortalGoalPlan task={{...planned,goal:{...planned.goal,steps:steps.map((step,i)=>i===1?{...step,status:'completed'}:step)}}} active/>)
  expect(trigger).toHaveAttribute('aria-expanded','false')
  expect(screen.getByRole('status')).toHaveTextContent('2/3')
  fireEvent.click(trigger)
  expect(screen.getByRole('list')).toBeVisible()
})

it('marks unfinished running steps as paused and retains the real resume action',()=>{
  const resume=vi.fn()
  const {rerender}=render(<PortalGoalPlan task={planned} onResume={resume} disabled/>)
  expect(screen.getByText('Paused')).toBeVisible()
  expect(screen.getByText('Build the page').closest('li')).toHaveAttribute('data-state','paused')
  expect(screen.getByRole('button',{name:'Continue goal'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Continue goal'}))
  expect(resume).not.toHaveBeenCalled()
  rerender(<PortalGoalPlan task={planned} onResume={resume}/>)
  fireEvent.click(screen.getByRole('button',{name:'Continue goal'}))
  expect(resume).toHaveBeenCalledOnce()
})

it('uses completed status only when the validated plan reports every task completed',()=>{
  const {rerender}=render(<PortalGoalPlan task={{...planned,goal:{...planned.goal,status:'completed',steps:steps.map(step=>({...step,status:'completed'}))}}} onResume={vi.fn()}/>)
  expect(screen.getByText('Plan completed')).toBeVisible()
  expect(screen.getByRole('status')).toHaveTextContent('3/3')
  expect(screen.queryByRole('button',{name:'Continue goal'})).toBeNull()
  rerender(<PortalGoalPlan task={{...planned,goal:{...planned.goal,status:'completed'}}}/>)
  expect(screen.queryByRole('region')).toBeNull()
})

it('distinguishes waiting and blocked plans and invents no initial steps',()=>{
  const {rerender}=render(<PortalGoalPlan task={{...planned,goal:{...planned.goal,status:'waiting'}}} active/>)
  expect(screen.getByText('Build the page').closest('li')).toHaveAttribute('data-state','waiting')
  rerender(<PortalGoalPlan task={{...planned,goal:{...planned.goal,status:'blocked',steps:[{id:'build',title:'Build the page',status:'blocked'}]}}}/>)
  expect(screen.getByText('Needs attention')).toBeVisible()
  expect(screen.getByText('Build the page').closest('li')).toHaveAttribute('data-state','blocked')
  rerender(<PortalGoalPlan task={{...planned,goal:{...planned.goal,steps:[]}}} active/>)
  expect(screen.getByRole('status')).toHaveTextContent('Planning')
  expect(screen.queryAllByRole('listitem')).toHaveLength(0)
})

it('uses only successful public steps without inventing a resumable goal',()=>{
  const display=steps=>({type:'steps',label:'Work plan',detail:null,sources:[],steps,change:null})
  const events=[{sequence:1,kind:'unknown',state:'completed',startedAt:time,finishedAt:time,display:display(steps)},{sequence:2,kind:'unknown',state:'failed',startedAt:time,finishedAt:time,display:display([{id:'fiction',title:'This update failed',status:'completed'}])}]
  render(<PortalGoalPlan task={{...task,calls:2,failures:1,activities:[{kind:'unknown',calls:2,failures:1,blocked:0}],events}} active onResume={vi.fn()}/>)
  expect(screen.getByText('Build the page')).toBeVisible()
  expect(screen.queryByText('This update failed')).toBeNull()
  expect(screen.queryByRole('button',{name:'Continue goal'})).toBeNull()
})

it('hides invalid data and resets a collapsed list for a different run',()=>{
  const {rerender}=render(<PortalGoalPlan task={planned}/>)
  fireEvent.click(screen.getByRole('button',{name:'Toggle tasks'}))
  rerender(<PortalGoalPlan task={{...planned,runId:'chatcmpl_aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'}}/>)
  expect(screen.getByRole('button',{name:'Toggle tasks'})).toHaveAttribute('aria-expanded','true')
  rerender(<PortalGoalPlan task={{...planned,privateThinking:'untrusted'}}/>)
  expect(screen.queryByRole('region')).toBeNull()
  rerender(<PortalGoalPlan task={task}/>)
  expect(screen.queryByRole('region')).toBeNull()
})
