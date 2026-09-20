import {fireEvent, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import PixelTaskActivity from './PixelTaskActivity'
import {parseTaskActivity, parseTaskActivityFrame} from '../lib/pixelTaskActivity'
import {parseTaskActivity as hostParse} from '../../../pixel-agent/host/task_activity_schema.mjs'

const task={schemaVersion:1,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:'2026-09-08T20:00:00.000Z',finishedAt:'2026-09-08T20:00:02.000Z',state:'completed',calls:2,failures:1,blocked:1,truncated:false,activities:[{kind:'read',calls:1,failures:0,blocked:0},{kind:'edit',calls:1,failures:1,blocked:1}]}
it('renders observed categories and errors without claiming task success',()=>{
  render(<PixelTaskActivity messages={[{role:'user',content:'Build my page'},{role:'assistant',content:'Done',task}]}/> )
  expect(screen.getByText('Read')).toBeVisible()
  expect(screen.getByText('Edit')).toBeVisible()
  expect(screen.getByText('1 failed · 1 blocked before execution')).toBeVisible()
  expect(screen.getByRole('status')).toHaveTextContent('Turn ended · 2s')
})
it('does not present an older run as the current turn',()=>{
  render(<PixelTaskActivity messages={[{role:'user',content:'Earlier task'},{role:'assistant',task},{role:'user',content:'New task'},{role:'assistant',content:'Created all files'}]}/> )
  expect(screen.queryByText('Read')).toBeNull()
  fireEvent.change(screen.getByRole('combobox'),{target:{value:'1'}})
  expect(screen.getByText('Read')).toBeVisible()
})
it('keeps model claims out of activity and marks live summaries as pending',()=>{
  render(<PixelTaskActivity sending elapsed="0:12" messages={[{role:'assistant',content:'I ran 99 tests'}]}/> )
  expect(screen.getByRole('status')).toHaveTextContent('Working · 0:12')
  expect(screen.queryByText(/99/)).toBeNull()
})
it('shares strict schema behavior with the host and rejects nonterminal/cross-run metadata',()=>{
  const cases=[task,{...task,secret:'no'},{...task,calls:513},{...task,activities:[...task.activities,...task.activities]},{...task,finishedAt:'2026-02-31T20:00:00.000Z'}]
  for(const value of cases) expect(parseTaskActivity(value,task.runId)).toEqual(hostParse(value,task.runId))
  expect(parseTaskActivityFrame({id:task.runId,pixel_task:task,choices:[{finish_reason:'stop'}]})).toEqual(task)
  expect(parseTaskActivityFrame({id:'other',pixel_task:task,choices:[{finish_reason:'stop'}]})).toBeNull()
  expect(parseTaskActivityFrame({id:task.runId,pixel_task:task,choices:[{finish_reason:null}]})).toBeNull()
})

it('accepts only explicit live observation packets and renders tools during the turn',()=>{
  const live={...task,state:'running',finishedAt:null}
  const packet={object:'ods.task.activity',id:task.runId,pixel_task:live}
  expect(parseTaskActivityFrame(packet)).toEqual(live)
  expect(parseTaskActivityFrame({...packet,prompt:'secret'})).toBeNull()
  expect(parseTaskActivityFrame({...packet,pixel_task:task})).toBeNull()
  render(<PixelTaskActivity sending elapsed="0:05" messages={[{role:'assistant',task:live}]}/> )
  expect(screen.getByText('Read')).toBeVisible()
  expect(screen.getByRole('status')).toHaveTextContent('Working · 0:05')
})
