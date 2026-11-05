import {act, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import PixelAccessCard from '../components/settings/PixelAccessCard'
import {VERIFY_HINT_DELAY_MS} from '../components/PortalRecovery'

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

it('refreshes already-open verified Permissions when Portal discovers recovery without polling idle access',async()=>{
 const sandbox={available:true,surface:'wsl-systemd',configured_mode:'sandboxed',effective_mode:'sandboxed',runtime_verified:true,revision:'a'.repeat(64),busy:false,pending:false}
 let held=false
 const transport=vi.fn(async(url,options)=>{
  if(url==='/api/pixel/status')return json(held?{available:false,state:'chat_recovery_required'}:{available:true})
  if(url==='/api/pixel/access-mode'){
   if(options?.method==='POST')held=false
   return json(held?{...sandbox,reason:'chat-recovery-required'}:sandbox)
  }
  if(url==='/api/pixel/chat/context')return json({schemaVersion:1,status:'missing',sessionRevision:null,
   context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
  return json({})
 })
 vi.stubGlobal('fetch',transport);vi.useFakeTimers()
 render(<><Pixel/><PixelAccessCard/></>);await act(async()=>{})
 const permissions=within(screen.getByRole('region',{name:'Portal permissions'}))
 fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Keep the unsent recovery draft'}})
 await act(async()=>{await vi.advanceTimersByTimeAsync(12000)})
 expect(transport.mock.calls.filter(([url])=>url==='/api/pixel/access-mode')).toHaveLength(1)
 expect(permissions.getByRole('button',{name:'Enable Full Access'})).toBeEnabled()
 held=true
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(permissions.getByRole('alert')).toHaveTextContent('new messages are held')
 expect(permissions.getByText('Effective').nextElementSibling).toHaveTextContent('Sandbox')
 expect(permissions.getByRole('button',{name:'Enable Full Access'})).toBeDisabled()
 expect(screen.getByPlaceholderText('Messages are held until Portal recovery')).toHaveValue('Keep the unsent recovery draft')
 expect(transport.mock.calls.filter(([url])=>url==='/api/pixel/access-mode')).toHaveLength(2)
 // An unchanged Portal status must not restart a permissions read every three seconds.
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(transport.mock.calls.filter(([url])=>url==='/api/pixel/access-mode')).toHaveLength(2)
 expect(transport.mock.calls.some(([url,options])=>url==='/api/pixel/access-mode'&&options?.method==='POST')).toBe(false)
 fireEvent.click(permissions.getByRole('button',{name:'Verify Sandbox'}));await act(async()=>{})
 expect(transport.mock.calls.filter(([url,options])=>url==='/api/pixel/access-mode'&&options?.method==='POST')).toHaveLength(1)
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(permissions.queryByRole('alert')).toBeNull()
 expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
 expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Keep the unsent recovery draft')
 expect(transport.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
})

it('recovers a held chat from Portal itself with the same Verify Sandbox request',async()=>{
 const sandbox={available:true,surface:'wsl-systemd',configured_mode:'sandboxed',effective_mode:'sandboxed',runtime_verified:true,revision:'a'.repeat(64),busy:false,pending:false,reason:null,scope:'owner-host'}
 let held=true
 const transport=vi.fn(async(url,options)=>{
  if(url==='/api/pixel/status')return json(held?{available:false,state:'chat_recovery_required',recovery:'verify-sandbox',
   detail:'Portal stopped during an earlier turn, so new messages are held until its permissions are checked again.'}:{available:true})
  if(url==='/api/pixel/access-mode'){
   if(options?.method==='POST'){held=false;return json(sandbox)}
   return json({...sandbox,reason:'chat-recovery-required'})
  }
  if(url==='/api/pixel/chat/context')return json({schemaVersion:1,status:'missing',sessionRevision:null,
   context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
  return json({})
 })
 vi.stubGlobal('fetch',transport);vi.useFakeTimers()
 render(<Pixel/>);await act(async()=>{})
 fireEvent.change(screen.getByPlaceholderText('Messages are held until Portal recovery'),{target:{value:'Keep this draft'}})
 expect(screen.getByText('Recovery required')).toBeVisible()
 const recovery=within(screen.getByRole('region',{name:'Portal recovery'}))
 expect(recovery.getByRole('alert')).toHaveTextContent('new messages are held')
 expect(screen.queryByRole('link',{name:'Portal permissions'})).toBeNull()
 // Recovery waits for the owner; polling alone never sends it.
 await act(async()=>{await vi.advanceTimersByTimeAsync(9000)})
 expect(transport.mock.calls.some(([url,options])=>options?.method==='POST'&&url!=='/api/pixel/chat/context')).toBe(false)
 fireEvent.click(recovery.getByRole('button',{name:'Verify now'}));await act(async()=>{})
 const posts=transport.mock.calls.filter(([url,options])=>url==='/api/pixel/access-mode'&&options?.method==='POST')
 expect(posts.map(([,options])=>JSON.parse(options.body))).toEqual([{mode:'sandboxed',revision:'a'.repeat(64),confirmed:false}])
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.queryByRole('region',{name:'Portal recovery'})).toBeNull()
 expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Keep this draft')
 expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
 expect(transport.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
})

it('offers Verify now for a stale Sandbox proof instead of a settings link',async()=>{
 const unverified={...failed(),state:'unverified',accessState:'unverified',reasonCode:'access-proof-unverified'}
 let status={available:true,readiness:unverified,recovery:'verify-sandbox'}
 transport(()=>status)
 vi.useFakeTimers()
 render(<Pixel/>);await act(async()=>{})
 // The host usually re-checks it within seconds, so the notice waits rather
 // than flashing after every reply (laptop, 2026-10-31).
 expect(screen.queryByRole('region',{name:'Portal recovery'})).toBeNull()
 await act(async()=>{await vi.advanceTimersByTimeAsync(VERIFY_HINT_DELAY_MS)})
 const recovery=within(screen.getByRole('region',{name:'Portal recovery'}))
 expect(recovery.getByRole('status')).toHaveTextContent('ODS usually re-checks it on its own; if this message stays, you can check it now.')
 expect(recovery.getByRole('button',{name:'Verify now'})).toBeEnabled()
 expect(screen.queryByLabelText('Runtime readiness')).toBeNull()
 // Chat stays usable meanwhile; the notice is not a hold.
 expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
 // The host monitor re-proves it on its own; the notice then goes away.
 status={available:true,readiness:{...unverified,accessState:'verified',effectiveMode:'sandboxed',reasonCode:'release-binding-unavailable'}}
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.queryByRole('region',{name:'Portal recovery'})).toBeNull()
 expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/access-mode')).toBe(false)
})

it('ends a refused Verify at the next status poll once a reply has withdrawn the hint',async()=>{
 const stale={available:true,surface:'wsl-systemd',configured_mode:'sandboxed',effective_mode:'unknown',runtime_verified:false,
  revision:'a'.repeat(64),busy:true,pending:false,reason:'runtime-proof-required',scope:'owner-host'}
 let hint=true,answer
 const read=new Promise(done=>{answer=done})
 const transport=vi.fn(async url=>{
  if(url==='/api/pixel/status')return json({available:true,...(hint?{recovery:'verify-sandbox'}:{})})
  if(url==='/api/pixel/access-mode')return read
  if(url==='/api/pixel/chat/context')return json({schemaVersion:1,status:'missing',sessionRevision:null,
   context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
  return json({})
 })
 vi.stubGlobal('fetch',transport);vi.useFakeTimers()
 render(<Pixel/>);await act(async()=>{})
 await act(async()=>{await vi.advanceTimersByTimeAsync(VERIFY_HINT_DELAY_MS)})
 fireEvent.click(within(screen.getByRole('region',{name:'Portal recovery'})).getByRole('button',{name:'Verify now'}))
 await act(async()=>{})
 // A reply starts while the check is in flight, so the status stops naming it.
 hint=false
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 await act(async()=>{answer(json(stale))})
 expect(within(screen.getByRole('region',{name:'Portal recovery'})).getByRole('alert'))
  .toHaveTextContent('Portal is still finishing earlier work')
 // The next poll changes nothing else, and still ends it.
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(screen.queryByRole('region',{name:'Portal recovery'})).toBeNull()
 expect(transport.mock.calls.some(([url,options])=>url==='/api/pixel/access-mode'&&options?.method==='POST')).toBe(false)
})

it('keeps the changed-files alert up while a stale Sandbox hint comes and goes with each reply',async()=>{
 // Two separate problems: an out-of-date sandbox check and changed runtime files.
 const mismatch=()=>({...failed(),accessState:'unverified',releaseState:'mismatch',
  reasonCode:'runtime-files-changed',observedAt:new Date().toISOString()})
 let recovery='verify-sandbox'
 transport(()=>({available:true,readiness:mismatch(),...(recovery?{recovery}:{})}))
 vi.useFakeTimers()
 render(<Pixel/>);await act(async()=>{})
 const alert=()=>screen.getByRole('alert',{name:'Runtime readiness'})
 const notice=()=>screen.queryByRole('region',{name:'Portal recovery'})
 expect(alert()).toHaveTextContent('Runtime files changed')
 await act(async()=>{await vi.advanceTimersByTimeAsync(VERIFY_HINT_DELAY_MS)})
 expect(notice()).not.toBeNull()
 expect(alert()).toHaveTextContent('Runtime files changed')
 // A reply runs, so the hint goes; the alert stays. Before, the two swapped.
 recovery=null
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(notice()).toBeNull()
 expect(alert()).toHaveTextContent('Runtime files changed')
 // Idle again: the alert is unchanged, and the hint waits its time again.
 recovery='verify-sandbox'
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
 expect(alert()).toHaveTextContent('Runtime files changed')
 expect(notice()).toBeNull()
})

it('lets Restore Sandbox stand in for the alert about the same unfinished change',async()=>{
 const transitioning={...failed(),accessState:'transitioning',reasonCode:'access-transition-pending'}
 transport(()=>({available:true,readiness:transitioning,recovery:'restore-sandbox'}))
 render(<Pixel/>)
 const recovery=within(await screen.findByRole('region',{name:'Portal recovery'}))
 expect(recovery.getByRole('button',{name:'Restore Sandbox'})).toBeEnabled()
 expect(screen.queryByLabelText('Runtime readiness')).toBeNull()
 expect(screen.getByText('Needs attention')).toBeVisible()
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
