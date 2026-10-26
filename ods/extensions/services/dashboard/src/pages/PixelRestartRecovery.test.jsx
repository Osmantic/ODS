// Seen on the RTX Spark: an update recreated the dashboard and its API and
// restarted Portal's gateway while "Can you do things on cron timers?"
// streamed. The chat showed "Ended early", "No tool activity was recorded"
// and "The response could not be received", the next message failed the same
// way, and the page then sat behind "Activity unknown" with a disabled
// "Resolve interrupted turn" while it polled three endpoints every second.
import {act,fireEvent,screen,waitFor,within} from '@testing-library/react'
import {render} from '../test/test-utils'
// The repository's base ESLint profile does not mark JSX identifiers as uses.
// eslint-disable-next-line no-unused-vars
import Pixel, {NO_REPLY_NOTICE, RESTART_NOTICE} from './Pixel'
import {CONTINUE_FAILED} from '../lib/portalContext'

const QUESTION='Can you do things on cron timers or scheduled work tasks?'
const PARTIAL='Yes. Portal can run'
const STALE=/Ended early|No tool activity was recorded|could not be received|check the connection|Activity unknown|could not be confirmed|Resolve interrupted turn|Check activity again|Try Stop previous work|Completion was not confirmed/
const STOPPED='Stopped by you. Workspace changes completed before cancellation were preserved.'
const STILL_WORKING='Portal is still working on your last request. You can send your next message when it’s done.'
const CONTINUE={name:'Continue in this chat'}
const response=(body,status=200)=>({ok:status>=200 && status<300,status,json:async()=>body})
const encode=value=>new TextEncoder().encode(`data: ${typeof value==='string'?value:JSON.stringify(value)}\n\n`)
const task={schemaVersion:1,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:'2026-10-26T01:34:20.000Z',finishedAt:null,state:'running',calls:0,failures:0,blocked:0,truncated:false,activities:[]}
const context=(history='ready')=>({schemaVersion:1,status:history==='unknown'?'unavailable':'ready',sessionRevision:null,context:null,model:null,
  compaction:{status:'idle',count:0},history:{status:history,revision:null,acknowledgedMessages:0}})
// A turn whose connection drops when the dashboard API is recreated.
function cutOffStream() {
  const chunks=[encode({object:'ods.task.activity',id:task.runId,pixel_task:task}),encode({choices:[{delta:{content:PARTIAL}}]})]
  return {ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>({
    read:async()=>{
      if(chunks.length)return {done:false,value:chunks.shift()}
      throw new TypeError('network error')
    },releaseLock(){},
  })}}
}
const answer=text=>{
  const chunks=[encode({choices:[{delta:{content:text}}]}),encode('[DONE]')]
  return {ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>({
    read:async()=>chunks.length?{done:false,value:chunks.shift()}:{done:true},releaseLock(){},
  })}}
}
const stored=()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
const field=()=>screen.getByPlaceholderText('Message Portal...')
const row=()=>screen.queryByRole('status',{name:'Last message'})

let server,fetchMock
const calls=url=>fetchMock.mock.calls.filter(([called])=>called===url)
beforeEach(()=>{
  localStorage.clear()
  server={result:()=>response({state:'unknown',events:''}),activity:()=>response({state:'unknown'}),cancel:()=>response({aborted:true}),
    context:()=>response(context()),streams:[]}
  fetchMock=vi.fn(async(url,options)=>{
    if(url==='/api/pixel/status')return response({available:true})
    if(url==='/api/pixel/chat/context')return server.context(options)
    if(url==='/api/pixel/chat/result')return server.result(JSON.parse(options.body),options)
    if(url==='/api/pixel/chat/activity')return server.activity(JSON.parse(options.body))
    if(url==='/api/pixel/chat/cancel')return server.cancel(JSON.parse(options.body))
    if(url==='/api/pixel/chat/stream')return (server.streams.shift() || (()=>answer('Done')))(JSON.parse(options.body))
    return response({})
  })
  vi.stubGlobal('fetch',fetchMock)
})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()})

function saveCutOffChat(extra={}) {
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'spark_chat',requestId:'checking-in',inFlight:false,interrupted:true,
    messages:[{role:'user',content:QUESTION},{role:'assistant',content:PARTIAL}],...extra}))
}

it('settles a turn a restart cut off by itself, keeps what arrived and offers Send again',async()=>{
  let attempt
  server.streams.push(body=>{attempt=body;return cutOffStream()})
  // The restarted server proved nothing still runs and settled the receipt:
  // the producer died with the old process, so no terminal frame was saved.
  server.result=body=>{
    expect(body).toEqual({chat_id:attempt.chat_id,request_id:attempt.request_id})
    return response({state:'interrupted',events:`data: ${JSON.stringify({choices:[{delta:{content:PARTIAL}}]})}\n\n`})
  }
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(field(),{target:{value:QUESTION}})
  fireEvent.click(screen.getByTitle('Send'))
  const reply=await screen.findByText((_,node)=>node?.tagName==='EM' && node.textContent===RESTART_NOTICE)
  const bubble=reply.closest('[data-pixel-response]')
  expect(within(bubble).getByText('Response stopped')).toBeVisible()
  expect(within(bubble).getByText(PARTIAL,{exact:false})).toBeVisible()
  expect(screen.queryByText(STALE)).toBeNull()
  expect(row()).toBeNull()
  // The composer is free and holds the question again.
  expect(field()).toBeEnabled()
  expect(field()).toHaveValue(QUESTION)
  expect(screen.getByTitle('Send')).toBeEnabled()
  await waitFor(()=>expect(stored()).toMatchObject({interrupted:false,inFlight:false,requestId:null}))
  expect(stored().messages.at(-1)).toMatchObject({status:'stopped',content:`${PARTIAL}\n\n_${RESTART_NOTICE}_`})
  expect(stored().messages.at(-1).task).toBeUndefined()
  // One click sends the question again as a new turn.
  server.streams.push(()=>answer('Yes: use a scheduled task.'))
  fireEvent.click(within(bubble).getByRole('button',{name:'Send again'}))
  expect(await screen.findByText('Yes: use a scheduled task.')).toBeVisible()
  const resent=JSON.parse(calls('/api/pixel/chat/stream')[1][1].body)
  expect(resent.messages.at(-1)).toEqual({role:'user',content:QUESTION})
  expect(resent.request_id).not.toBe(attempt.request_id)
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
})

it('frees the Spark’s window whose own request has no receipt once the server settles the chat',async()=>{
  // The window kept the refused "Checking in" request, which never got a
  // receipt, while an older dead attempt held the chat on the server.
  saveCutOffChat({draft:'checking in'})
  server.result=body=>{expect(body).toEqual({chat_id:'spark_chat',request_id:'checking-in'});return response({state:'unknown',events:''})}
  server.activity=()=>response({state:'terminal'})
  vi.useFakeTimers()
  await act(async()=>{render(<Pixel/>)})
  await act(async()=>vi.advanceTimersByTimeAsync(10))
  expect(screen.getByText('Available')).toBeVisible()
  // The work may well be done, so the reply says so plainly and asks for
  // nothing to be repeated; the owner's own draft stays as it was.
  expect(screen.getByText((_,node)=>node?.tagName==='EM' && node.textContent===NO_REPLY_NOTICE)).toBeVisible()
  expect(row()).toBeNull()
  expect(field()).toBeEnabled()
  expect(field()).toHaveValue('checking in')
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(screen.queryByRole('button',{name:'Send again'})).toBeNull()
  expect(screen.queryByText(STALE)).toBeNull()
  // A proven state ends the checks: nothing is polled any more.
  const checks=calls('/api/pixel/chat/result').length+calls('/api/pixel/chat/activity').length
  await act(async()=>vi.advanceTimersByTimeAsync(5*60_000))
  expect(calls('/api/pixel/chat/result').length+calls('/api/pixel/chat/activity').length).toBe(checks)
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
  expect(stored()).toMatchObject({interrupted:false,requestId:null})
})

it('backs off instead of polling every second while a turn cannot be proven',async()=>{
  saveCutOffChat()
  vi.useFakeTimers()
  await act(async()=>{render(<Pixel/>)})
  await act(async()=>vi.advanceTimersByTimeAsync(3*60_000))
  // Main checked /result and /activity every 2 s: about 90 times each here.
  expect(calls('/api/pixel/chat/activity').length).toBeLessThanOrEqual(9)
  expect(calls('/api/pixel/chat/result').length).toBeLessThanOrEqual(9)
  expect(calls('/api/pixel/chat/context').length).toBeLessThanOrEqual(3)
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
})

it('keeps one working Continue in this chat while activity stays unknown, and frees the composer when the server confirms',async()=>{
  saveCutOffChat({draft:'checking in'})
  render(<Pixel/>);await screen.findByText('Available')
  const hold=await screen.findByRole('status',{name:'Last message'})
  await within(hold).findByText('Portal can’t confirm your last request has finished.')
  const proceed=within(hold).getByRole('button',{name:'Continue in this chat'})
  expect(proceed).toBeEnabled()
  expect(screen.queryByText(STALE)).toBeNull()
  // The draft stays editable, and sending waits for the owner's choice.
  expect(field()).toHaveValue('checking in')
  expect(screen.getByTitle('Send')).toBeDisabled()
  fireEvent.keyDown(field(),{key:'Enter'})
  expect(screen.getByText('Choose Continue in this chat first. Your message is kept here.')).toBeVisible()
  // The server's history lock is unusable at first (an empty lock file left
  // by a crash), so it cannot confirm: the control stays and links help.
  server.cancel=()=>response({detail:'Portal cancellation is unavailable'},503)
  fireEvent.click(proceed)
  await within(hold).findByText(CONTINUE_FAILED)
  expect(within(hold).getByRole('link',{name:/Get help on Discord/})).toBeVisible()
  expect(within(hold).getByRole('button',{name:'Continue in this chat'})).toBeEnabled()
  expect(screen.getByRole('button',{name:'New chat'})).not.toHaveAttribute('aria-disabled')
  // Once it can, its confirmation alone releases the hold, even though
  // the activity lookup still answers unknown. Continue stops anything left
  // over, so the reply says it was stopped, never that Portal finished.
  server.cancel=body=>{expect(body).toEqual({chat_id:'spark_chat'});return response({aborted:true})}
  fireEvent.click(within(hold).getByRole('button',{name:'Continue in this chat'}))
  await waitFor(()=>expect(row()).toBeNull())
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(2)
  expect(screen.getByText(STOPPED)).toBeVisible()
  expect(screen.queryByText(NO_REPLY_NOTICE)).toBeNull()
  expect(stored().messages.at(-1)).toMatchObject({status:'stopped',content:`${PARTIAL}\n\n---\n\n_${STOPPED}_`})
  expect(field()).toBeEnabled()
  expect(field()).toHaveValue('checking in')
  expect(screen.getByTitle('Send')).toBeEnabled()
  await waitFor(()=>expect(stored()).toMatchObject({interrupted:false,requestId:null}))
  server.streams.push(()=>answer('I am here.'))
  fireEvent.click(screen.getByTitle('Send'))
  expect(await screen.findByText('I am here.')).toBeVisible()
  expect(calls('/api/pixel/chat/stream')).toHaveLength(1)
})

it('offers Continue in this chat only once a check cannot tell, never while that check runs',async()=>{
  saveCutOffChat()
  // The restarting API does not answer; each check gives up after 15 s.
  server.result=(_body,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))))
  vi.useFakeTimers()
  await act(async()=>{render(<Pixel/>)})
  await act(async()=>vi.advanceTimersByTimeAsync(10))
  // Continue stops anything left over, and this check may still find work running.
  expect(within(row()).getByText('Checking on your last request…')).toBeVisible()
  expect(within(row()).queryByRole('button',CONTINUE)).toBeNull()
  await act(async()=>vi.advanceTimersByTimeAsync(14_000))
  expect(within(row()).queryByRole('button',CONTINUE)).toBeNull()
  await act(async()=>vi.advanceTimersByTimeAsync(1_000))
  expect(within(row()).getByText('Portal can’t confirm your last request has finished.')).toBeVisible()
  expect(within(row()).getByRole('button',CONTINUE)).toBeEnabled()
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
})

it('never offers Continue while the server finds this turn still running',async()=>{
  saveCutOffChat()
  let state='active'
  server.activity=()=>response({state})
  vi.useFakeTimers()
  await act(async()=>{render(<Pixel/>)})
  await act(async()=>vi.advanceTimersByTimeAsync(10))
  // This turn's reply was cut off, so its answer cannot appear here; the line
  // does not promise that it will.
  expect(within(row()).getByText('Portal is still working on your last request. When it’s done, ask Portal what it did. You can stop it.')).toBeVisible()
  expect(screen.queryByRole('button',CONTINUE)).toBeNull()
  expect(screen.getByTitle('Stop')).toBeEnabled()
  for(let second=0;second<2*60;second++)await act(async()=>vi.advanceTimersByTimeAsync(1000))
  expect(screen.queryByRole('button',CONTINUE)).toBeNull()
  // It was checked at least every 15 s, and the end of that work frees the chat.
  expect(calls('/api/pixel/chat/activity').length).toBeGreaterThanOrEqual(8)
  state='terminal'
  for(let second=0;second<15;second++)await act(async()=>vi.advanceTimersByTimeAsync(1000))
  expect(row()).toBeNull()
  expect(screen.getByText((_,node)=>node?.tagName==='EM' && node.textContent===NO_REPLY_NOTICE)).toBeVisible()
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
})

it('says Portal is still working, with no Continue, when it finds the chat busy, and frees the chat when that work ends',async()=>{
  let history='unknown',state='active'
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'history_chat',messages:[{role:'user',content:QUESTION},{role:'assistant',content:'Earlier answer',status:'done'}],draft:'checking in'}))
  server.context=()=>response({...context(history),status:history==='unknown'?'busy':'ready'})
  server.activity=body=>{expect(body).toEqual({chat_id:'history_chat'});if(state==='terminal')history='ready';return response({state})}
  vi.useFakeTimers()
  await act(async()=>{render(<Pixel/>)})
  await act(async()=>vi.advanceTimersByTimeAsync(1_100))
  expect(within(row()).getByText(STILL_WORKING)).toBeVisible()
  expect(screen.queryByRole('button',CONTINUE)).toBeNull()
  expect(screen.getByTitle('Send')).toBeDisabled()
  fireEvent.keyDown(field(),{key:'Enter'})
  expect(screen.getByText('Portal is not ready for a new message yet. Your message is kept here; send it when Portal is ready.')).toBeVisible()
  // It stays that way while the work runs, checked at least every 15 s.
  for(let second=0;second<2*60;second++)await act(async()=>vi.advanceTimersByTimeAsync(1000))
  expect(within(row()).getByText(STILL_WORKING)).toBeVisible()
  expect(screen.queryByRole('button',CONTINUE)).toBeNull()
  expect(calls('/api/pixel/chat/activity').length).toBeGreaterThanOrEqual(8)
  state='terminal'
  for(let second=0;second<15;second++)await act(async()=>vi.advanceTimersByTimeAsync(1000))
  expect(row()).toBeNull()
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(field()).toHaveValue('checking in')
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
  expect(screen.queryByText(STALE)).toBeNull()
})

it('does not blame the connection when an older turn holds the chat, and sends the new message once it is cleared up',async()=>{
  // The window believes the chat is free, but the server still holds an
  // older dead attempt and refuses the new one before starting it.
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'spark_chat',requestId:null,inFlight:false,interrupted:false,
    messages:[{role:'user',content:QUESTION},{role:'assistant',content:'Earlier answer',status:'done'}]}))
  let settled=false
  server.streams.push(()=>response({detail:'This conversation has an unresolved attempt; recover or stop it first'},423))
  server.activity=()=>response({state:settled?'terminal':'unknown'})
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(field(),{target:{value:'Checking in'}})
  fireEvent.click(screen.getByTitle('Send'))
  const hold=await screen.findByRole('status',{name:'Last message'})
  await waitFor(()=>expect(hold).toHaveTextContent('Your new message will go out right after.'))
  expect(await within(hold).findByRole('button',{name:'Continue in this chat'})).toBeEnabled()
  expect(screen.queryByText(STALE)).toBeNull()
  // The refused message is not left in the conversation; it waits in the composer.
  expect(within(screen.getByRole('region',{name:'Conversation messages'})).queryByText('Checking in')).toBeNull()
  expect(field()).toHaveValue('Checking in')
  // The server then proves the older attempt is over: the held message goes out.
  settled=true
  server.streams.push(()=>answer('Hi, I am here.'))
  expect(await screen.findByText('Hi, I am here.',{},{timeout:4000})).toBeVisible()
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
  const sent=calls('/api/pixel/chat/stream').map(([,options])=>JSON.parse(options.body).messages.at(-1))
  expect(sent).toEqual([{role:'user',content:'Checking in'},{role:'user',content:'Checking in'}])
  expect(screen.getByText('Earlier answer')).toBeVisible()
  expect(screen.queryByText(STALE)).toBeNull()
})

it('heals an unconfirmed history by itself when the server proves the chat idle',async()=>{
  let unknown=true
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'history_chat',messages:[{role:'user',content:QUESTION},{role:'assistant',content:'Earlier answer',status:'done'}],draft:'checking in'}))
  server.context=()=>response(context(unknown?'unknown':'ready'))
  server.activity=body=>{expect(body).toEqual({chat_id:'history_chat'});unknown=false;return response({state:'terminal'})}
  render(<Pixel/>);await screen.findByText('Available')
  await screen.findByRole('status',{name:'Last message'})
  await waitFor(()=>expect(row()).toBeNull(),{timeout:4000})
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
  expect(screen.queryByText(STALE)).toBeNull()
})

it('asks once, in place, when an unconfirmed history cannot be proven idle',async()=>{
  let unknown=true
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'history_chat',messages:[{role:'user',content:QUESTION},{role:'assistant',content:'Earlier answer',status:'done'}],draft:'checking in'}))
  server.context=()=>response(context(unknown?'unknown':'ready'))
  server.cancel=body=>{expect(body).toEqual({chat_id:'history_chat'});unknown=false;return response({aborted:true})}
  render(<Pixel/>);await screen.findByText('Available')
  const hold=await screen.findByRole('status',{name:'Last message'})
  const proceed=await within(hold).findByRole('button',{name:'Continue in this chat'})
  await within(hold).findByText('Portal can’t confirm your last request has finished.',{},{timeout:4000})
  expect(screen.getByTitle('Send')).toBeDisabled()
  fireEvent.click(proceed)
  await waitFor(()=>expect(row()).toBeNull())
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(1)
})
