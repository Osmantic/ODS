import {act, cleanup, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

afterEach(() => {cleanup(); vi.unstubAllGlobals()})

it('paints tool progress before any answer and retains failed final verification', async () => {
  localStorage.clear()
  let stream
  const body = new ReadableStream({start(controller) {stream = controller}})
  vi.stubGlobal('fetch', vi.fn(async url => String(url).endsWith('/chat/stream')
    ? {ok:true, headers:new Map([['content-type','text/event-stream']]), body}
    : {ok:true, json:async () => ({available:true})}))
  render(<Pixel/>)
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'), {target:{value:'Repair main.js'}})
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(() => expect(screen.getAllByTitle('Portal · thinking')).toHaveLength(2))
  const startedAt = new Date().toISOString()
  const task = {
    schemaVersion:4, runId:'chatcmpl_11111111-2222-4333-8444-555555555555',
    startedAt, finishedAt:null, state:'running', calls:1, failures:0, blocked:0,
    truncated:false, activities:[{kind:'read',calls:1,failures:0,blocked:0}],
    events:[{sequence:1,kind:'read',state:'running',startedAt,finishedAt:null,
      display:{type:'tool',label:'Reading a file',detail:'main.js',sources:[],steps:[],change:null}}],
    context:null, goal:null, projects:[],
  }
  const emit = frame => act(async () => {
    stream.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(frame)}\n\n`))
  })
  // Only an actual tool observation: no model narration or answer delta.
  await emit({object:'ods.task.activity',id:task.runId,pixel_task:task})
  expect(await screen.findByText('Reading a file')).toBeVisible()
  expect(screen.getByText('main.js')).toBeVisible()
  expect(screen.queryByTitle('Portal · done')).toBeNull()
  await emit({error:{message:'Final verification failed'}})
  await act(async () => stream.close())
  expect(await screen.findByText('Portal could not complete the response.')).toBeVisible()
  expect(screen.getByRole('button',{name:'Needs attention'})).toBeVisible()
  expect(screen.getByText('Unconfirmed')).toBeVisible()
  expect(screen.queryByTitle('Portal · done')).toBeNull()
})
