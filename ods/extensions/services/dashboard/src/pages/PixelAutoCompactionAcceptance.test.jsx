import {act,fireEvent,screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {saveConversation,SELECT_EVENT} from '../lib/pixelConversations'

// Real Pixel page + context hook + HTTP JSON contract; no hook/state mocks.
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body})
const runtime={source:'remote-provider',model:'cloud-test',contextLength:8192,maxTokens:2048,reasoning:false,routeFingerprint:'a'.repeat(64)}
const ownerMessages=[{role:'user',content:'Remember Cedar.'},{role:'assistant',content:'Cedar remembered.',status:'done'}]
const operation='history-'+ 'b'.repeat(64)
const context=(state='running',used=1200)=>({schemaVersion:1,status:'ready',sessionRevision:'native-first',
  model:{id:'cloud-test',provider:'remote',contextWindow:8192,routeFingerprint:runtime.routeFingerprint},
  context:{used,window:8192,measuredAt:'2026-09-30T11:00:00.000Z'},
  compaction:{status:state==='runtime-restarted'?'unknown':state,requestId:operation,count:state==='completed'?1:0,...(['failed','runtime-restarted'].includes(state)?{reason:state==='failed'?'runtime-failed':state}:{})},
  history:{revision:'c'.repeat(64),acknowledgedMessages:2}})
const seed=()=>localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'first',messages:ownerMessages,draft:'Keep my next request'}))
const ring=used=>`${Math.round(used*100/8192)}% full · ${used.toLocaleString()} / ${(8192).toLocaleString()} tokens used`
const calls=url=>fetch.mock.calls.filter(([path])=>path===url)
const tick=async ms=>act(async()=>vi.advanceTimersByTimeAsync(ms))
beforeEach(()=>{vi.useFakeTimers();localStorage.clear();vi.stubGlobal('fetch',vi.fn())})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()})

it.each(['completed','failed','runtime-restarted'])('automatic compaction read failure recovers %s through the real page and read-only transport',async terminal=>{
  seed();let phase='running'
  fetch.mockImplementation(async url=>url==='/api/pixel/chat/context'
    ? phase==='503'?response({detail:'temporarily unavailable'},503):response(context(phase,phase==='completed'?300:1200))
    :response({available:true,runtime}))
  render(<Pixel/>);await act(async()=>{})
  const field=screen.getByPlaceholderText('Message Portal...')
  expect(screen.getByText('Compacting conversation context…')).toBeVisible()
  expect(field).toBeDisabled();expect(field).toHaveValue('Keep my next request')
  expect(screen.getByRole('button',{name:ring(1200)})).toBeVisible()
  phase='503';await tick(2000)
  expect(screen.getByText('Waiting for compaction confirmation…')).toBeVisible()
  expect(screen.getByText('Checking context')).toBeVisible()
  expect(field).toBeDisabled()
  phase=terminal;await tick(5000)
  const expected=terminal==='completed'?'Context compacted. Your full conversation is preserved.':terminal==='failed'?'Context compaction failed. Your conversation is preserved. The runtime could not complete the operation.':'Runtime restarted before compaction could be confirmed. Your conversation is preserved; you can continue.'
  expect(screen.getByText(expected)).toBeVisible()
  expect(field).toBeEnabled();expect(screen.getByTitle('Send')).toBeEnabled()
  expect(field).toHaveValue('Keep my next request')
  expect(screen.getByText('Cedar remembered.')).toBeVisible()
  expect(screen.getByRole('button',{name:terminal==='completed'?ring(300):ring(1200)})).toBeVisible()
  const reads=calls('/api/pixel/chat/context').length
  await tick(10000)
  expect(calls('/api/pixel/chat/context')).toHaveLength(reads)
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  for(const [,options] of calls('/api/pixel/chat/context'))expect(JSON.parse(options.body).chat_id).toBe('first')
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))).toMatchObject({chatId:'first',messages:ownerMessages,draft:'Keep my next request',compactionRequestId:null})
})

it('discards a late old-session HTTP response after actual conversation selection',async()=>{
  saveConversation({schema:1,chatId:'second',messages:[{role:'user',content:'Second owner request'},{role:'assistant',content:'Second conversation.',status:'done'}],draft:'Second draft'})
  seed();let phase='running',finishLate,lateSignal
  fetch.mockImplementation(async(url,options)=>{
    if(url!=='/api/pixel/chat/context')return response({available:true,runtime})
    const id=JSON.parse(options.body).chat_id
    if(id==='second')return response({...context('idle',700),sessionRevision:'native-second',compaction:{status:'idle',count:0}})
    if(phase==='late')return new Promise(resolve=>{finishLate=()=>resolve(response(context('running',7900)));lateSignal=options.signal})
    return response(context(phase))
  })
  render(<Pixel/>);await act(async()=>{})
  phase='completed';await tick(2000)
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  await tick(1600);phase='late'
  fireEvent.mouseEnter(screen.getByRole('button',{name:ring(1200)}));await act(async()=>{})
  expect(finishLate).toBeTypeOf('function')
  await act(async()=>window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'second'})))
  expect(lateSignal.aborted).toBe(true)
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Second draft')
  expect(screen.getByRole('button',{name:ring(700)})).toBeVisible()
  await act(async()=>finishLate())
  await tick(10000)
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Second draft')
  expect(screen.getByText('Second conversation.')).toBeVisible()
  expect(screen.queryByText('Compacting conversation context…')).toBeNull()
  expect(screen.queryByText('Context compacted. Your full conversation is preserved.')).toBeNull()
  expect(screen.getByRole('button',{name:ring(700)})).toBeVisible()
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
})
