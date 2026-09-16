import {act, cleanup, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

afterEach(() => {cleanup();vi.unstubAllGlobals()})

it('drives both mascots from validated live task activity counters', async () => {
  localStorage.clear()
  let stream
  const body = new globalThis.ReadableStream({start(controller) {stream = controller}})
  vi.stubGlobal('fetch', vi.fn(async url => String(url).endsWith('/chat/stream')
    ? {ok:true,headers:new Map([['content-type','text/event-stream']]),body}
    : {ok:true,json:async () => ({available:true})}))
  render(<Pixel/>)
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Read a file'}})
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(() => expect(screen.getAllByTitle('Portal · thinking')).toHaveLength(2))
  const task = {
    schemaVersion:1,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',
    startedAt:'2026-09-12T02:00:00.000Z',finishedAt:null,state:'running',
    calls:0,failures:0,blocked:0,truncated:false,activities:[],
  }
  const emit = frame => act(async () => {
    stream.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(frame) + '\n\n'))
  })
  await emit({object:'ods.task.activity',id:task.runId,pixel_task:task})
  expect(screen.getAllByTitle('Portal · thinking')).toHaveLength(2)
  // Unknown fields are rejected by the closed telemetry schema.
  await emit({object:'ods.task.activity',id:task.runId,pixel_task:{...task,tools:{started:1}}})
  expect(screen.getAllByTitle('Portal · thinking')).toHaveLength(2)
  await emit({object:'ods.task.activity',id:task.runId,pixel_task:{
    ...task,calls:1,activities:[{kind:'read',calls:1,failures:0,blocked:0}],
  }})
  await waitFor(() => expect(screen.getAllByTitle('Portal · working')).toHaveLength(2))
  await emit({choices:[{delta:{content:'Read complete.'}}]})
  await emit({id:task.runId,choices:[{delta:{},finish_reason:'stop'}],pixel_task:{
    ...task,state:'completed',finishedAt:'2026-09-12T02:00:01.000Z',
    calls:1,activities:[{kind:'read',calls:1,failures:0,blocked:0}],
  }})
  await act(async () => {
    stream.enqueue(new TextEncoder().encode('data: [DONE]\n\n'))
    stream.close()
  })
  expect(await screen.findByTitle('Portal · done')).toBeInTheDocument()
  expect(screen.getAllByTitle('Portal · idle').length).toBeGreaterThan(0)
  expect(screen.queryAllByTitle('Portal · working')).toHaveLength(0)
})
