import {act, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

const json = data => ({ok:true, json:async()=>data})
const failed = () => ({schemaVersion:1, state:'attention', routeAvailable:true,
  accessState:'failed', effectiveMode:'unknown', releaseState:'unverified',
  reasonCode:'access-inspection-failed', observedAt:new Date().toISOString()})

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function transport(getStatus) {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/pixel/status') return json(getStatus())
    if (url === '/api/pixel/chat/context') return json({schemaVersion:1,status:'missing',sessionRevision:null,
      context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
    return json({})
  }))
}

it('holds a retained draft for interrupted chat recovery and resumes only after fresh idle status',async()=>{
 let status={available:true}
 transport(()=>status)
 vi.useFakeTimers()
 render(<Pixel/>);await act(async()=>{})
 fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Keep this draft through recovery'}})
 status={available:false,state:'chat_recovery_required',detail:'Portal stopped during an earlier turn and new messages are held.'}
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.getByText('Recovery required')).toBeVisible()
 const composer=screen.getByPlaceholderText('Messages are held until Portal recovery')
 expect(composer).toBeDisabled();expect(composer).toHaveValue('Keep this draft through recovery')
 expect(screen.getByRole('link',{name:'Portal permissions'})).toHaveAttribute('href','/settings?section=access')
 expect(screen.queryByText('Available')).toBeNull()
 expect(fetch.mock.calls.some(call=>call[0]==='/api/pixel/chat/stream')).toBe(false)
 status={available:true}
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
 expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Keep this draft through recovery')
 expect(fetch.mock.calls.some(call=>call[0]==='/api/pixel/chat/stream')).toBe(false)
})

it('retains only a diagnostic cloud label during an available access transition and recovers on polling',async()=>{
  const runtime={source:'remote-provider',model:'cloud-model',contextLength:65536,maxTokens:4096,reasoning:false}
  let status={available:true,runtime}
  transport(()=>status)
  vi.useFakeTimers()
  render(<Pixel/>)
  await act(async()=>{})
  expect(screen.getByRole('button',{name:'Choose model: cloud model'})).toBeVisible()
  status={available:true,runtime:null,readiness:{...failed(),accessState:'transitioning',reasonCode:'access-transition-pending'}}
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
  expect(screen.getByRole('button',{name:'Last confirmed model: cloud model; model unverified'})).toBeVisible()
  expect(screen.getByRole('alert',{name:'Runtime readiness'})).toHaveTextContent('An access transition is unfinished')
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  status={available:true,runtime:{...runtime,model:'recovered-cloud-model'}}
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
  expect(screen.getByRole('button',{name:'Choose model: recovered cloud model'})).toBeVisible()
  expect(screen.queryByRole('button',{name:/Last confirmed/})).toBeNull()
})

it('makes failed access conspicuous while retaining existing safe chat controls', async () => {
  transport(() => ({available:true, model:'pixel/default', runtime:{model:'Mac fixture',contextLength:65536,source:'local-switchboard'}, readiness:failed()}))
  render(<Pixel/>)
  const notice = await screen.findByRole('alert', {name:'Runtime readiness'})
  expect(notice).toHaveTextContent('host access inspection failed')
  expect(screen.getByText('Needs attention')).toBeVisible()
  expect(within(notice).getByRole('link', {name:'Access settings'})).toHaveAttribute('href','/settings?section=access')
  const composer = screen.getByPlaceholderText('Message Portal...')
  expect(composer).toBeEnabled()
  fireEvent.change(composer, {target:{value:'Explain this concept'}})
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(screen.queryByText('Ready')).toBeNull()
  expect(notice).not.toHaveTextContent(/admission|held/)
})

it('legacy available status stays usable without a persistent readiness warning', async () => {
  transport(() => ({available:true, detail:'Owner agent ready'}))
  render(<Pixel/>)
  await screen.findByText('Available')
  expect(screen.queryByLabelText('Runtime readiness')).toBeNull()
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  expect(screen.queryByText('Ready')).toBeNull()
  expect(screen.getByText('Available')).not.toHaveClass('text-emerald-400')
})

it('keeps ordinary unverified readiness out of chat and still shows subsequent actionable failure', async () => {
  let status = {available:true, readiness:{...failed(),state:'unverified',accessState:'verified',
    effectiveMode:'sandboxed',reasonCode:'release-binding-unavailable'}}
  transport(() => status)
  vi.useFakeTimers()
  render(<Pixel/>)
  await act(async () => {})
  expect(screen.queryByLabelText('Runtime readiness')).toBeNull()
  status = {available:true}
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByLabelText('Runtime readiness')).toBeNull()
  status = {available:true, readiness:failed()}
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByRole('alert', {name:'Runtime readiness'})).toHaveTextContent('host access inspection failed')
  status = {available:true}
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByLabelText('Runtime readiness')).toBeNull()
})

it.each([
  ['unfinished access transition', {accessState:'transitioning',reasonCode:'access-transition-pending'}, 'An access transition is unfinished'],
  ['changed runtime files', {accessState:'verified',effectiveMode:'sandboxed',releaseState:'mismatch',reasonCode:'runtime-files-changed'}, 'Runtime files changed'],
])('retains the actionable warning for %s', async (_label, fields, detail) => {
  transport(() => ({available:true, readiness:{...failed(),...fields}}))
  render(<Pixel/>)
  expect(await screen.findByRole('alert', {name:'Runtime readiness'})).toHaveTextContent(detail)
  expect(screen.getByText('Needs attention')).toBeVisible()
})
