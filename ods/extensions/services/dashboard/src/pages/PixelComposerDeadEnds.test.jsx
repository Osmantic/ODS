/* global atob */
// Dead ends seen on the demo machine: "New task" did nothing during a build,
// a question typed then was lost, and a picture sent while Portal was busy
// failed with a generic error. Each test drives the real composer and sidebar.
import {act,fireEvent,screen,waitFor,within} from '@testing-library/react'
import {render} from '../test/test-utils'
// The repository's base ESLint profile does not mark JSX identifiers as uses.
// eslint-disable-next-line no-unused-vars
import Pixel from './Pixel'
// eslint-disable-next-line no-unused-vars
import PixelConversationNavigation from '../components/PixelConversationNavigation'
import {OPEN_PIXEL_SEARCH} from '../components/PixelCommandSearch'
import {sha256} from '../lib/pixelArtifacts'
import {restoreUnsentDraft} from '../lib/pixelComposerText'
import {busyRefusalText,isAdmissionRejected,isBusyRefusal,portalResponseFailure} from '../lib/portalResponseFailure'
import {ATTACH_BLOCKED,newTaskBlockedReason,publishNewTaskBlocked,sendBlockedReason} from '../lib/portalTaskState'

const NEW_TASK_WAITS=newTaskBlockedReason({running:true})
const SEND_WAITS=sendBlockedReason({running:true,ready:true})
const BUSY={error:{type:'pixel_dashboard_error',code:'image_turn_busy'}}
const bytes=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR9kAAAAASUVORK5CYII='),character=>character.charCodeAt(0))
const imageId=`img-${'1'.repeat(32)}`,fingerprint='a'.repeat(64)
const runtime={source:'external-host',model:'vision-model',contextLength:32768}
const snapshot=()=>({schemaVersion:1,status:'ready',sessionRevision:'revision',context:{used:100,window:32768},
  model:{id:'vision-model',provider:'ods-gateway',contextWindow:32768,imageInput:'supported',routeFingerprint:fingerprint},
  compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
const response=(body,status=200)=>({ok:status>=200 && status<300,status,json:async()=>body})
const frame=value=>new TextEncoder().encode(`data: ${typeof value==='string'?value:JSON.stringify(value)}\n\n`)
const sse=values=>{
  const chunks=values.map(frame)
  return {ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>({
    read:async()=>chunks.length?{done:false,value:chunks.shift()}:{done:true},releaseLock(){},
  })}}
}
const answer=text=>sse([{choices:[{delta:{content:text}}]},'[DONE]'])
// A build that keeps running until the test finishes it.
function build(text='Stopwatch page ready') {
  let finish,started=false
  const gate=new Promise(resolve=>{finish=resolve})
  return {finish:()=>finish(),response:{ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>({
    read:async()=>{
      if(started)return {done:true}
      started=true;await gate
      return {done:false,value:new TextEncoder().encode(`data: ${JSON.stringify({choices:[{delta:{content:text}}]})}\n\ndata: [DONE]\n\n`)}
    },releaseLock(){},
  })}}}
}
let receipt,streams,fetchMock
const calls=url=>fetchMock.mock.calls.filter(([called])=>called===url)
const uploads=()=>fetchMock.mock.calls.filter(([url,options])=>url.startsWith('/api/pixel/images/') && options?.method==='POST')
const stored=()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
const field=()=>screen.getByPlaceholderText('Message Portal...')
const picture=()=>new File([bytes],'sketch.png',{type:'image/png'})
async function ready(ui=<Pixel/>) {render(ui);await screen.findByText('Available')}
async function attach() {
  fireEvent.change(screen.getByLabelText('Choose images'),{target:{files:[picture()]}})
  await screen.findByText(/1 × 1 · Ready/)
}
async function startBuild(turn) {
  streams.push(()=>turn.response)
  fireEvent.change(field(),{target:{value:'Build a stopwatch page'}})
  fireEvent.click(screen.getByTitle('Send'))
  await screen.findByTitle('Stop')
}

beforeEach(async()=>{
  localStorage.clear()
  publishNewTaskBlocked('')
  HTMLDialogElement.prototype.showModal=function(){this.open=true}
  HTMLDialogElement.prototype.close=function(){this.open=false}
  receipt={id:imageId,sha256:await sha256(bytes),media_type:'image/png',bytes:bytes.length,width:1,height:1}
  streams=[]
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:local-picture')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  fetchMock=vi.fn(async(url,options)=>{
    if(url==='/api/pixel/status')return response({available:true,runtime})
    if(url==='/api/pixel/chat/context')return response(snapshot())
    if(url==='/api/pixel/chat/stream')return (streams.shift() || (()=>answer('Done')))(options)
    if(url.startsWith('/api/pixel/images/') && options?.method==='POST')return response(receipt,201)
    return response({})
  })
  vi.stubGlobal('fetch',fetchMock)
})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})

describe('pure rules', ()=>{
  it('returns a turn that did not start to the composer without replacing newer text', ()=>{
    expect(restoreUnsentDraft('', 'Build a stopwatch')).toBe('Build a stopwatch')
    expect(restoreUnsentDraft('  ', 'Build a stopwatch')).toBe('Build a stopwatch')
    expect(restoreUnsentDraft('What time is it?', 'Build a stopwatch')).toBe('Build a stopwatch\n\nWhat time is it?')
    expect(restoreUnsentDraft('Build a stopwatch', 'Build a stopwatch')).toBe('Build a stopwatch')
    // Restoring twice never duplicates the unsent message.
    const once=restoreUnsentDraft('What time is it?', 'Build a stopwatch')
    expect(restoreUnsentDraft(once, 'Build a stopwatch')).toBe(once)
    expect(restoreUnsentDraft('Keep me', '')).toBe('Keep me')
  })
  it('names why New task and Send wait, and nothing when they do not', ()=>{
    expect(NEW_TASK_WAITS).toBe('Portal is finishing the current task; you can start a new one when it’s done, or stop it.')
    expect(newTaskBlockedReason({running:true,stopping:true})).toMatch(/stopping the current task/)
    for(const state of ['checking','launching','compacting'])expect(newTaskBlockedReason({[state]:true})).toMatch(/in a moment/)
    expect(newTaskBlockedReason({})).toBe('')
    expect(SEND_WAITS).toMatch(/Your message is kept here/)
    expect(sendBlockedReason({stopping:true,running:true,ready:true})).toMatch(/stopping/)
    expect(sendBlockedReason({waiting:true,ready:true})).toMatch(/not ready/)
    expect(sendBlockedReason({ready:false})).toMatch(/not ready/)
    expect(sendBlockedReason({deleted:true,running:true})).toBe('')
    expect(sendBlockedReason({ready:true})).toBe('')
  })
  it('reads a busy picture refusal calmly, as a turn that never started', ()=>{
    expect(isBusyRefusal(BUSY.error)).toBe(true)
    expect(isAdmissionRejected(BUSY.error)).toBe(true)
    expect(isBusyRefusal({type:'pixel_ingress_error',code:'image_turn_busy'})).toBe(false)
    expect(portalResponseFailure(BUSY.error)).toBe(busyRefusalText(true))
    expect(busyRefusalText(true)).toMatch(/another picture.*did not start.*kept/)
    for(const text of [busyRefusalText(true),busyRefusalText(false)])expect(text).not.toMatch(/could not complete|failed|error/i)
  })
})

describe('sidebar New task', ()=>{
  it.each([false,true])('looks disabled with the reason while Portal is busy, and a click still asks (collapsed=%s)', collapsed=>{
    const asked=vi.fn()
    window.addEventListener('ods:pixel-new-task',asked)
    render(<PixelConversationNavigation collapsed={collapsed}/>)
    const button=screen.getByRole('button',{name:'New task'})
    expect(button).not.toHaveAttribute('aria-disabled')
    act(()=>publishNewTaskBlocked(NEW_TASK_WAITS))
    expect(button).toHaveAttribute('aria-disabled','true')
    if(collapsed)expect(button).toHaveAttribute('title',NEW_TASK_WAITS)
    else {
      expect(button).toHaveAccessibleDescription(NEW_TASK_WAITS)
      expect(screen.getByText(NEW_TASK_WAITS)).toBeVisible()
    }
    // A tap on a phone has no tooltip: the click reaches Portal, which says why.
    fireEvent.click(button)
    expect(asked).toHaveBeenCalledTimes(1)
    act(()=>publishNewTaskBlocked(''))
    expect(button).not.toHaveAttribute('aria-disabled')
    expect(screen.queryByText(NEW_TASK_WAITS)).toBeNull()
    window.removeEventListener('ods:pixel-new-task',asked)
  })
})

describe('Portal during a build', ()=>{
  it('never leaves New task inert: it waits with a reason, then starts a new chat', async()=>{
    const turn=build()
    await ready(<><nav aria-label="Rail"><PixelConversationNavigation collapsed={false}/></nav><Pixel/></>)
    await startBuild(turn)
    const rail=within(screen.getByRole('navigation',{name:'Rail'}))
    const newTask=rail.getByRole('button',{name:'New task'})
    expect(newTask).toHaveAttribute('aria-disabled','true')
    expect(newTask).toHaveAccessibleDescription(NEW_TASK_WAITS)
    expect(screen.getByRole('button',{name:'New chat'})).toHaveAttribute('aria-disabled','true')
    expect(screen.getByRole('button',{name:'New chat'})).toHaveAttribute('title',NEW_TASK_WAITS)
    // Clicking it anyway explains beside the composer and keeps the build open.
    fireEvent.click(newTask)
    expect(screen.getAllByRole('alert').some(alert=>alert.textContent===NEW_TASK_WAITS)).toBe(true)
    expect(screen.getAllByText('Build a stopwatch page').length).toBeGreaterThan(0)
    expect(screen.getByTitle('Stop')).toBeEnabled()
    await act(async()=>turn.finish())
    await screen.findByText('Stopwatch page ready')
    await waitFor(()=>expect(newTask).not.toHaveAttribute('aria-disabled'))
    expect(rail.queryByText(NEW_TASK_WAITS)).toBeNull()
    fireEvent.click(newTask)
    await screen.findByText('What do you want to work on?')
    const conversation=within(screen.getByRole('region',{name:'Conversation messages'}))
    expect(conversation.queryByText('Build a stopwatch page')).toBeNull()
    // The build's chat stays in the rail, finished and ready to reopen.
    expect(rail.getByRole('button',{name:'Build a stopwatch page'})).toBeVisible()
  })

  it('keeps the question typed during the build, saves it, and says why Enter waits', async()=>{
    const turn=build()
    await ready()
    await startBuild(turn)
    expect(field()).toBeEnabled()
    fireEvent.change(field(),{target:{value:'What time is it in Lisbon?'}})
    fireEvent.keyDown(field(),{key:'Enter'})
    expect(screen.getByText(SEND_WAITS)).toBeVisible()
    expect(field()).toHaveValue('What time is it in Lisbon?')
    expect(calls('/api/pixel/chat/stream')).toHaveLength(1)
    await waitFor(()=>expect(stored().draft).toBe('What time is it in Lisbon?'))
    await act(async()=>turn.finish())
    await screen.findByText('Stopwatch page ready')
    expect(field()).toHaveValue('What time is it in Lisbon?')
    await waitFor(()=>expect(screen.queryByText(SEND_WAITS)).toBeNull())
    fireEvent.keyDown(field(),{key:'Enter'})
    await waitFor(()=>expect(calls('/api/pixel/chat/stream')).toHaveLength(2))
    expect(JSON.parse(calls('/api/pixel/chat/stream')[1][1].body).messages.at(-1).content).toBe('What time is it in Lisbon?')
  })

  it('inserts command search prompts into the draft and shows why New task waits there too', async()=>{
    const turn=build()
    await ready()
    await startBuild(turn)
    act(()=>window.dispatchEvent(new Event(OPEN_PIXEL_SEARCH)))
    const search=within(screen.getByRole('dialog',{name:'Search Portal'}))
    expect(search.getByRole('button',{name:/^New task\s*Portal is finishing the current task/})).toBeVisible()
    fireEvent.click(search.getByRole('button',{name:/Plan deep work/}))
    expect(field().value).toMatch(/^Build a durable plan for this goal/)
    await act(async()=>turn.finish())
  })

  it.each(['composer','conversation'])('never lets a picture dropped on the %s replace the page, and says why it waits', async target=>{
    const turn=build()
    await ready()
    await startBuild(turn)
    const zone=target==='composer' ? field().closest('.portal-glass-composer') : screen.getByRole('region',{name:'Conversation messages'})
    // A cancelled dragover/drop is what keeps the browser from opening the file.
    expect(fireEvent.dragOver(zone,{dataTransfer:{types:['Files']}})).toBe(false)
    expect(fireEvent.drop(zone,{dataTransfer:{types:['Files'],files:[picture()]}})).toBe(false)
    expect(screen.getAllByRole('alert').some(alert=>alert.textContent===ATTACH_BLOCKED)).toBe(true)
    expect(uploads()).toHaveLength(0)
    await act(async()=>turn.finish())
    await screen.findByText('Stopwatch page ready')
    fireEvent.drop(zone,{dataTransfer:{types:['Files'],files:[picture()]}})
    await screen.findByText(/1 × 1 · Ready/)
    expect(uploads()).toHaveLength(1)
  })

  it('says why a pasted picture waits, without losing pasted text', async()=>{
    const turn=build()
    await ready()
    await startBuild(turn)
    const pasted=fireEvent.paste(field(),{clipboardData:{items:[{kind:'file',getAsFile:picture}]}})
    expect(pasted).toBe(true)
    expect(screen.getAllByRole('alert').some(alert=>alert.textContent===ATTACH_BLOCKED)).toBe(true)
    expect(uploads()).toHaveLength(0)
    await act(async()=>turn.finish())
  })
})

describe('busy refusals', ()=>{
  it('reads a busy picture refusal calmly, keeps the message and picture, and sends again in one click', async()=>{
    streams.push(()=>sse([BUSY,'[DONE]']))
    await ready();await attach()
    fireEvent.change(field(),{target:{value:'Make this sketch a web page'}})
    fireEvent.click(screen.getByTitle('Send'))
    const notice=await screen.findByRole('status',{name:'Message not started'})
    expect(notice).toHaveTextContent(busyRefusalText(true))
    expect(screen.queryByText(/could not complete|Request failed|could not be received|Completion was not confirmed|Get help on Discord/)).toBeNull()
    expect(field()).toHaveValue('Make this sketch a web page')
    expect(screen.getByText(/1 × 1 · Ready/)).toBeVisible()
    // The refused turn never started, so it is not left in the conversation.
    expect(screen.getByText('What do you want to work on?')).toBeVisible()
    await waitFor(()=>expect(stored()).toMatchObject({requestId:null,inFlight:false,interrupted:false,messages:[],draft:'Make this sketch a web page',draftImages:[receipt]}))
    expect(calls('/api/pixel/chat/activity')).toHaveLength(0)
    streams.push(()=>answer('Your sketch is now a page'))
    fireEvent.click(within(notice).getByRole('button',{name:'Send again'}))
    await screen.findByText('Your sketch is now a page')
    const sent=JSON.parse(calls('/api/pixel/chat/stream')[1][1].body)
    expect(sent.messages).toEqual([{role:'user',content:'Make this sketch a web page',images:[{id:imageId,sha256:receipt.sha256}]}])
    expect(sent.history_snapshot.messages).toHaveLength(1)
    expect(screen.queryByRole('status',{name:'Message not started'})).toBeNull()
    await waitFor(()=>expect(screen.queryByRole('list',{name:'Attached images'})).toBeNull())
  })

  it('treats an HTTP 429 as busy, and keeps text typed while it was pending', async()=>{
    let release
    streams.push(()=>new Promise(resolve=>{release=()=>resolve(response({detail:'Portal image processing is busy. Try again shortly.'},429))}))
    await ready()
    fireEvent.change(field(),{target:{value:'Build a stopwatch page'}})
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByTitle('Stop')
    fireEvent.change(field(),{target:{value:'Add a lap button too'}})
    await act(async()=>release())
    const notice=await screen.findByRole('status',{name:'Message not started'})
    expect(notice).toHaveTextContent(busyRefusalText(false))
    expect(field()).toHaveValue('Build a stopwatch page\n\nAdd a lap button too')
    expect(screen.queryByText(/Request failed|could not be received|Completion was not confirmed|try again shortly/i)).toBeNull()
    await waitFor(()=>expect(stored()).toMatchObject({requestId:null,inFlight:false,interrupted:false}))
    expect(calls('/api/pixel/chat/activity')).toHaveLength(0)
    expect(calls('/api/pixel/chat/result')).toHaveLength(0)
  })

  it('restores a busy-refused turn found after a reload, calmly and without resending it', async()=>{
    const events=`data: ${JSON.stringify(BUSY)}\n\ndata: [DONE]\n\n`
    localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'busy-chat',requestId:'busy-attempt',inFlight:true,
      messages:[{role:'user',content:'Make this sketch a web page'},{role:'assistant',content:''}]}))
    const base=fetchMock.getMockImplementation()
    fetchMock.mockImplementation(async(url,options)=>url==='/api/pixel/chat/result'
      ? response({state:'rejected',events}) : base(url,options))
    await ready()
    await waitFor(()=>expect(field()).toHaveValue('Make this sketch a web page'))
    await waitFor(()=>expect(stored()).toMatchObject({requestId:null,inFlight:false,interrupted:false}))
    expect(stored().messages.at(-1).content).toBe(busyRefusalText(true))
    expect(screen.queryByText(/could not complete|Check saved work/)).toBeNull()
    expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  })
})
