import {fireEvent, render, screen} from '@testing-library/react'
import PortalContextRing from './PortalContextRing'
import PortalStreamingText from './PortalStreamingText'
import PortalGoalPlan from './PortalGoalPlan'
import PortalAgentActivity from './PortalAgentActivity'
import {parseTaskActivity} from '../lib/pixelTaskActivity'
import {continueGoal,goalCommand} from '../lib/portalGoal'
const time='2026-09-15T10:00:00.000Z'
const task={schemaVersion:2,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:time,finishedAt:time,state:'completed',calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'read',calls:1,failures:0,blocked:0}],events:[{sequence:1,kind:'read',state:'completed',startedAt:time,finishedAt:time}],context:{used:810,window:1000,measuredAt:time},goal:null}
it('shows measured occupancy on focus and touch, never a pretend zero',()=>{
  const {rerender}=render(<PortalContextRing context={task.context}/>)
  const button=screen.getByRole('button',{name:/81% full/})
  fireEvent.focus(button);expect(screen.getByRole('tooltip')).toHaveTextContent('81% used (19% remaining)');expect(screen.getByRole('tooltip')).toHaveTextContent('810 / 1k tokens used')
  fireEvent.keyDown(button,{key:'Escape'});expect(screen.queryByRole('tooltip')).toBeNull()
  rerender(<PortalContextRing capacity={32768}/>);fireEvent.click(screen.getByRole('button'))
  expect(screen.getByRole('tooltip')).toHaveTextContent('Token usage unavailable')
  expect(screen.queryByText('0%')).toBeNull()
})
it('keeps all streaming text immediately available and preserves exact safe source links',()=>{
  const {rerender}=render(<PortalStreamingText active instant>{'Answer with [evidence](https://example.com/source).'}</PortalStreamingText>)
  expect(screen.getByText(/Answer with/)).toBeVisible()
  expect(screen.getByRole('link')).toHaveAttribute('href','https://example.com/source')
  expect(screen.getByText(/Answer with/).closest('[aria-busy]')).toHaveAttribute('aria-busy','true')
  fireEvent.focus(screen.getByRole('link'));expect(screen.getByRole('tooltip')).toHaveTextContent('example.com')
  rerender(<PortalStreamingText instant>{'Done. [unsafe](javascript:alert(1))'}</PortalStreamingText>)
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText(/Done/)).toBeVisible()
})
it('animates appended text without remounting earlier chunks or discarding code',()=>{
  const {container,rerender}=render(<PortalStreamingText active instant>Hello</PortalStreamingText>)
  const first=container.querySelector('.portal-stream-reveal')
  rerender(<PortalStreamingText active instant>{'Hello world\n\n```js\nconst n = 1;\n```'}</PortalStreamingText>)
  expect(container.querySelector('p')).toHaveTextContent('Hello world')
  expect(container.querySelector('.portal-stream-reveal')).toBe(first)
  expect(container.querySelector('code')).toHaveTextContent('const n = 1;')
  expect(container.querySelector('code .portal-stream-reveal')).toBeNull()
  rerender(<PortalStreamingText instant>{'Hello world\n\n```js\nconst n = 1;\n```'}</PortalStreamingText>)
  expect(container.querySelector('.portal-stream-reveal')).toBe(first)
  expect(container.querySelector('[aria-busy]')).toHaveAttribute('aria-busy','false')
})
it('renders observed steps and pending work without equating tool success to completion',()=>{
  expect(parseTaskActivity(task,task.runId)).toBeTruthy()
  render(<PortalAgentActivity task={task} active/>)
  expect(screen.getByRole('list',{name:'Execution steps'})).toHaveTextContent('Reading files')
  expect(screen.queryByText('Live activity')).toBeNull()
})
it('a stopped goal remains resumable and keeps the original objective',()=>{
  const resume=vi.fn(), goal={status:'active',summary:'Working',steps:[{id:'build',title:'Build the page',status:'running'}]}
  render(<PortalGoalPlan task={{...task,goal}} onResume={resume}/>)
  expect(screen.getByText('Paused')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Continue goal'}));expect(resume).toHaveBeenCalledOnce()
  expect(goalCommand('/goal Build the page').task).toBe('Build the page')
  expect(goalCommand('/goals Build')).toBeNull()
  expect(continueGoal([{role:'user',content:'/goal Build the page'},{role:'assistant',task:{goal}}],1,'Blue')).toContain('Original objective: Build the page')
})
