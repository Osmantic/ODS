import {act,fireEvent,screen,waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {CONTEXT_REQUEST_ID} from '../lib/portalContext'

const response=(body,status=200)=>({ok:status>=200 && status<300,status,json:async()=>body})
const runtime={source:'local-switchboard',model:'small-model',contextLength:8192}
const snapshot=(status='idle',requestId,used=1200)=>({schemaVersion:1,status:'ready',sessionRevision:'session-1',
  model:{id:'small-model',provider:'local',contextWindow:8192},context:{used,window:8192,measuredAt:'2026-09-16T12:00:00.000Z'},
  compaction:{status,count:status==='completed'?1:0,...(requestId?{requestId}:{})},history:{revision:'a'.repeat(64),acknowledgedMessages:2}})
const initial=[{role:'user',content:'Remember that the project is named Cedar.'},{role:'assistant',content:'The project is Cedar.',status:'done'}]
const seed=(extra={})=>localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'context-chat',messages:initial,draft:'',...extra}))
const stored=()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
const calls=url=>fetch.mock.calls.filter(([path])=>path===url)
const stream=()=>{
  const bytes=new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Continued successfully"}}]}\n\ndata: [DONE]\n\n')
  let read=false
  return {ok:true,status:200,body:{getReader:()=>({read:async()=>read?{done:true}:(read=true,{done:false,value:bytes}),releaseLock(){}})}}
}
beforeEach(()=>{localStorage.clear();globalThis.fetch=vi.fn()})
afterEach(()=>{vi.useRealTimers();vi.restoreAllMocks()})

it('keeps native usage through delayed status hydration for the same model with a different advertised capacity',async()=>{
  seed({draft:'Keep this draft'})
  let finishStatus
  const measured={...snapshot('idle',undefined,14320),model:{...snapshot().model,contextWindow:65536},
    context:{...snapshot('idle',undefined,14320).context,window:65536}}
  fetch.mockImplementation(url=>{
    if(url==='/api/pixel/status')return new Promise(resolve=>{finishStatus=()=>resolve(response({available:true,runtime:{...runtime,contextLength:32768}}))})
    return Promise.resolve(response(url==='/api/pixel/chat/context'?measured:{}))
  })
  render(<Pixel systemStatus={{inference:{loadedModel:'small-model',contextSize:65536}}}/>)
  const label='22% full · 14,320 / 65,536 tokens used'
  expect(await screen.findByRole('button',{name:label})).toBeVisible()
  await act(async()=>finishStatus())
  await screen.findByText('Available')
  expect(screen.getByRole('button',{name:label})).toBeVisible()
  expect(calls('/api/pixel/chat/context')).toHaveLength(1)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(stored()).toMatchObject({draft:'Keep this draft',messages:initial})
})

it.each(['model','provider'])('retains confirmed B through missing runtime and stale fallback A, but invalidates a confirmed %s switch',async switchKind=>{
  vi.useFakeTimers()
  seed({draft:'Preserve this draft'})
  let status={available:true,runtime}
  let measured=snapshot()
  fetch.mockImplementation(async url=>response(url==='/api/pixel/chat/context'?measured:url==='/api/pixel/status'?status:{}))
  render(<Pixel systemStatus={{inference:{loadedModel:'small-model',contextSize:8192}}}/>)
  await act(async()=>{})
  expect(screen.getByRole('button',{name:'15% full · 1,200 / 8,192 tokens used'})).toBeVisible()
  const confirmedB={...runtime,model:'model-b'}
  status={available:true,runtime:confirmedB}
  measured={...snapshot('idle',undefined,2400),model:{...snapshot().model,id:'model-b'}}
  await act(async()=>vi.advanceTimersByTimeAsync(3000))
  const measuredB='29% full · 2,400 / 8,192 tokens used'
  expect(screen.getByRole('button',{name:measuredB})).toBeVisible()
  const reads=calls('/api/pixel/chat/context').length
  for(const value of [{available:true},{available:false,state:'unavailable'},{available:true,runtime:confirmedB}]) {
    status=value
    await act(async()=>vi.advanceTimersByTimeAsync(3000))
    expect(screen.getByRole('button',{name:measuredB})).toBeVisible()
    if(!value.runtime) {
      fireEvent.click(screen.getByRole('button',{name:'Choose model: Choose model'}))
      await act(async()=>{})
      expect(screen.getByRole('dialog',{name:'Choose model'})).toHaveTextContent('The conversation’s model source is not confirmed.')
      fireEvent.click(screen.getByRole('button',{name:'Choose model: Choose model'}))
    }
  }
  expect(calls('/api/pixel/chat/context')).toHaveLength(reads)
  status={available:true,runtime:switchKind==='model'?{...confirmedB,model:'model-c'}
    :{...confirmedB,source:'remote-provider',maxTokens:2048,reasoning:false}}
  await act(async()=>vi.advanceTimersByTimeAsync(3000))
  expect(screen.queryByRole('button',{name:measuredB})).toBeNull()
  expect(screen.getByRole('button',{name:'Token usage unavailable · 8,192 token capacity'})).toBeVisible()
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(stored()).toMatchObject({draft:'Preserve this draft',messages:initial})
})

it.each(['/compact','/compactar'])('runs %s as a session operation without appending a chat turn',async command=>{
  seed();let current=snapshot()
  fetch.mockImplementation(async(url,options)=>{
    if(url==='/api/pixel/chat/compact'){current=snapshot('completed',JSON.parse(options.body).request_id,0);return response(current)}
    if(url==='/api/pixel/chat/context')return response(current)
    return response({available:true,runtime})
  })
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:command}})
  fireEvent.click(screen.getByTitle('Send'))
  await screen.findByText('Context compacted. Your full conversation is preserved.')
  const body=JSON.parse(calls('/api/pixel/chat/compact')[0][1].body)
  expect(body).toEqual({chat_id:'context-chat',request_id:expect.stringMatching(CONTEXT_REQUEST_ID)})
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(stored()).toMatchObject({chatId:'context-chat',messages:initial,draft:'',compactionRequestId:null})
  expect(screen.getByRole('button',{name:'0% full · 0 / 8,192 tokens used'})).toBeVisible()
})

it('uses the confirmed remote destination identity when two providers serve the same named model',async()=>{
  vi.useFakeTimers()
  seed({draft:'Keep this draft'})
  const routeA='a'.repeat(64),routeB='b'.repeat(64)
  let active={...runtime,source:'remote-provider',maxTokens:2048,reasoning:false,routeFingerprint:routeA}
  let measured={...snapshot(),model:{...snapshot().model,routeFingerprint:routeA}}
  fetch.mockImplementation(async url=>response(url==='/api/pixel/chat/context'?measured:url==='/api/pixel/status'?{available:true,runtime:active}:{}))
  render(<Pixel/>)
  await act(async()=>{})
  expect(screen.getByRole('button',{name:'15% full · 1,200 / 8,192 tokens used'})).toBeVisible()
  active={...active,routeFingerprint:routeB}
  await act(async()=>vi.advanceTimersByTimeAsync(3000))
  expect(screen.getByRole('button',{name:'Token usage unavailable · 8,192 token capacity'})).toBeVisible()
  measured={...snapshot('idle',undefined,300),model:{...snapshot().model,routeFingerprint:routeB}}
  await act(async()=>vi.advanceTimersByTimeAsync(1501))
  fireEvent.mouseEnter(screen.getByRole('button',{name:'Token usage unavailable · 8,192 token capacity'}))
  await act(async()=>{})
  expect(screen.getByRole('button',{name:'4% full · 300 / 8,192 tokens used'})).toBeVisible()
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(stored()).toMatchObject({draft:'Keep this draft',messages:initial})
})

it('keeps the draft and transcript while compacting and saves recovery identity before the POST',async()=>{
  seed({draft:'My next request'});let finish,current=snapshot()
  fetch.mockImplementation((url,options)=>{
    if(url==='/api/pixel/chat/compact') {
      const id=JSON.parse(options.body).request_id
      expect(stored().compactionRequestId).toBe(id)
      return new Promise(resolve=>{finish=()=>{current=snapshot('completed',id,900);resolve(response(current))}})
    }
    return Promise.resolve(response(url==='/api/pixel/chat/context'?current:{available:true,runtime}))
  })
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.click(screen.getByRole('button',{name:'Open prompt commands'}))
  fireEvent.click(screen.getByRole('button',{name:/Compact Free context/}))
  expect(screen.getByPlaceholderText('Message Portal...')).toBeDisabled()
  expect(screen.getByTitle('Start a new chat')).toBeDisabled()
  expect(stored()).toMatchObject({chatId:'context-chat',messages:initial,draft:'My next request'})
  await act(async()=>finish())
  await screen.findByText('Context compacted. Your full conversation is preserved.')
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('My next request')
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
})

it('reuses the exact request identity after an ambiguous error instead of starting a second compaction',async()=>{
  seed({draft:'/compact'});let attempts=0,current
  fetch.mockImplementation(async(url,options)=>{
    if(url==='/api/pixel/chat/compact') {
      attempts++;if(attempts===1)throw new Error('network disconnected')
      current=snapshot('completed',JSON.parse(options.body).request_id);return response(current)
    }
    if(url==='/api/pixel/chat/context'){if(!current)throw new Error('offline');return response(current)}
    return response({available:true,runtime})
  })
  render(<Pixel/>);await screen.findByText('Available');fireEvent.click(screen.getByTitle('Send'))
  await screen.findByRole('button',{name:'Retry request'})
  const pending=stored().compactionRequestId
  expect(pending).toMatch(CONTEXT_REQUEST_ID)
  expect(stored().messages).toEqual(initial)
  fireEvent.click(screen.getByRole('button',{name:'Retry request'}))
  await screen.findByText('Context compacted. Your full conversation is preserved.')
  expect(calls('/api/pixel/chat/compact').map(([,options])=>JSON.parse(options.body).request_id)).toEqual([pending,pending])
  expect(stored().compactionRequestId).toBeNull()
})

it('recovers a pending compaction after reload using context inspection only',async()=>{
  const id='11111111-2222-4333-8444-555555555555'
  seed({draft:'Keep this draft',compactionRequestId:id})
  fetch.mockImplementation(async url=>response(url==='/api/pixel/chat/context'?snapshot('completed',id,700):{available:true,runtime}))
  render(<Pixel/>)
  await screen.findByText('Context compacted. Your full conversation is preserved.')
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(calls('/api/pixel/chat/context').every(([,options])=>JSON.parse(options.body).chat_id==='context-chat')).toBe(true)
  expect(stored()).toMatchObject({chatId:'context-chat',draft:'Keep this draft',messages:initial,compactionRequestId:null})
})

it('does not compact an editor whose conversation changed in another tab',async()=>{
  seed({draft:'Local draft'})
  fetch.mockResolvedValue(response({available:true,runtime}))
  render(<Pixel/>);await screen.findByText('Available')
  const newer={...stored(),draft:'Other tab draft',messages:[...initial,{role:'user',content:'Newer request'}]}
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify(newer))
  fireEvent.click(screen.getByRole('button',{name:'Open prompt commands'}))
  fireEvent.click(screen.getByRole('button',{name:/Compact Free context/}))
  expect(await screen.findByRole('alert')).toHaveTextContent('changed in another tab')
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
  expect(stored()).toEqual(newer)
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Local draft')
})

it('sends the complete retained transcript beside bounded legacy messages without copying UI metadata',async()=>{
  const messages=Array.from({length:60},(_,index)=>({role:index%2?'assistant':'user',content:index===1?'Long reply '.repeat(2000):`Turn ${index}`,status:'done'}))
  seed({messages})
  fetch.mockImplementation(async url=>url==='/api/pixel/chat/stream'?stream():response(url==='/api/pixel/chat/context'?snapshot():{available:true,runtime}))
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Continue with Cedar'}})
  fireEvent.click(screen.getByTitle('Send'))
  // Rendering this deliberately large retained transcript competes with the
  // response reveal frames on slower Windows CI runners.
  await screen.findByText('Continued successfully',{}, {timeout:5000})
  const body=JSON.parse(calls('/api/pixel/chat/stream')[0][1].body)
  expect(body.messages.length).toBeLessThanOrEqual(50)
  expect(body.history_snapshot).toEqual({schemaVersion:1,messages:[...messages.map(({role,content})=>({role,content})),{role:'user',content:'Continue with Cedar'}]})
  expect(stored().messages).toHaveLength(62)
  expect(stored().messages[1].content).toEqual(messages[1].content)
})

it.each(['skipped','failed'])('reports %s without claiming context was compacted',async state=>{
  seed({draft:'/compact'});let current=snapshot()
  fetch.mockImplementation(async(url,options)=>{
    if(url==='/api/pixel/chat/compact')current={...snapshot(state,JSON.parse(options.body).request_id),compaction:{...snapshot(state,JSON.parse(options.body).request_id).compaction,reason:'Runtime decision'}}
    return response(url==='/api/pixel/chat/compact' || url==='/api/pixel/chat/context'?current:{available:true,runtime})
  })
  render(<Pixel/>);await screen.findByText('Available');fireEvent.click(screen.getByTitle('Send'))
  await screen.findByText(state==='skipped'?/No compaction was needed/:/Context compaction failed/)
  expect(screen.queryByText('Context compacted. Your full conversation is preserved.')).toBeNull()
  expect(stored().messages).toEqual(initial)
  expect(stored().compactionRequestId).toBeNull()
})

it('clears an unaccepted request when the API rejects compaction as busy',async()=>{
  seed({draft:'/compact'})
  fetch.mockImplementation(async url=>url==='/api/pixel/chat/compact'
    ?response({detail:'Recover the active response first'},423):response({available:true,runtime}))
  render(<Pixel/>);await screen.findByText('Available');fireEvent.click(screen.getByTitle('Send'))
  await screen.findByText('Compaction can start after the current task finishes.')
  expect(screen.queryByRole('button',{name:'Check status'})).toBeNull()
  expect(stored().compactionRequestId).toBeNull()
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('/compact')
  expect(calls('/api/pixel/chat/compact')).toHaveLength(1)
})

it('does not leave a brand new chat locked when no runtime session exists',async()=>{
  seed({messages:[],draft:'/compact'})
  const missing={...snapshot(),status:'missing',sessionRevision:null,context:null,model:null,history:{revision:null,acknowledgedMessages:0}}
  fetch.mockImplementation(async url=>response(url.includes('/api/pixel/chat/')?missing:{available:true,runtime}))
  render(<Pixel/>);await screen.findByText('Available');fireEvent.click(screen.getByTitle('Send'))
  await screen.findByText(/No runtime context/)
  expect(stored().compactionRequestId).toBeNull()
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  expect(stored().messages).toEqual([])
})

it.each([true,false])('resolves uncertain history only after an explicit stop and preserves the draft (acknowledged=%s)',async acknowledged=>{
  seed({draft:'Keep the next request'})
  let unknown=true,finish
  fetch.mockImplementation((url,options)=>{
    if(url==='/api/pixel/chat/cancel') {
      expect(JSON.parse(options.body)).toEqual({chat_id:'context-chat'})
      return new Promise(resolve=>{finish=()=>{unknown=!acknowledged;resolve(response({aborted:acknowledged}))}})
    }
    const context={...snapshot(),history:{...snapshot().history,status:unknown?'unknown':'ready'}}
    return Promise.resolve(response(url==='/api/pixel/chat/context'?context:{available:true,runtime}))
  })
  render(<Pixel/>);await screen.findByText('Available')
  const resolveButton=await screen.findByRole('button',{name:'Resolve interrupted turn'})
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(0)
  expect(screen.getByPlaceholderText('Message Portal...')).toBeDisabled()
  expect(screen.queryByText(/Context compaction failed/)).toBeNull()
  fireEvent.click(resolveButton)
  expect(resolveButton).toBeDisabled()
  expect(screen.getByTitle('Start a new chat')).toBeDisabled()
  await act(async()=>finish())
  if(acknowledged) {
    await waitFor(()=>expect(screen.queryByRole('button',{name:'Resolve interrupted turn'})).toBeNull())
    expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  }else {
    await screen.findByText(/The stop could not be confirmed/)
    expect(screen.getByPlaceholderText('Message Portal...')).toBeDisabled()
    expect(screen.getByRole('button',{name:'Resolve interrupted turn'})).toBeEnabled()
  }
  expect(stored()).toMatchObject({chatId:'context-chat',draft:'Keep the next request',messages:initial})
  expect(calls('/api/pixel/chat/cancel')).toHaveLength(1)
  expect(calls('/api/pixel/chat/stream')).toHaveLength(0)
  expect(calls('/api/pixel/chat/compact')).toHaveLength(0)
})

it('shows session token usage and capacity even when general model status reports a smaller fallback',async()=>{
  seed()
  let current={...snapshot(),model:{...snapshot().model,contextWindow:65536},context:{...snapshot().context,used:3590,window:65536}}
  fetch.mockImplementation(async(url,options)=>{
    if(url==='/api/pixel/chat/compact')current={...current,context:null,compaction:{status:'completed',count:1,requestId:JSON.parse(options.body).request_id}}
    return response(url.startsWith('/api/pixel/chat/')?current:{available:true,runtime:{...runtime,contextLength:32768}})
  })
  render(<Pixel/>);await screen.findByText('Available')
  expect(await screen.findByRole('button',{name:'5% full · 3,590 / 65,536 tokens used'})).toBeVisible()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'/compact'}})
  fireEvent.click(screen.getByTitle('Send'))
  await screen.findByText('Context compacted. Your full conversation is preserved.')
  expect(screen.getByRole('button',{name:'Token usage unavailable · 65,536 token capacity'})).toBeVisible()
})

it('automatically inspects the session after reload and ignores the generic installer capacity',async()=>{
  seed()
  const current={...snapshot(),model:{...snapshot().model,contextWindow:65536},context:{...snapshot().context,used:3590,window:65536}}
  fetch.mockImplementation(async url=>response(url==='/api/pixel/chat/context'?current:{available:true}))
  render(<Pixel systemStatus={{inference:{loadedModel:'small-model',contextSize:32768}}}/>);await screen.findByText('Available')
  const ring=await screen.findByRole('button',{name:'5% full · 3,590 / 65,536 tokens used'})
  expect(calls('/api/pixel/chat/context').length).toBeGreaterThan(0)
  fireEvent.click(ring)
  expect(await screen.findByRole('button',{name:'5% full · 3,590 / 65,536 tokens used'})).toBeVisible()
  expect(screen.getByRole('tooltip')).not.toHaveTextContent('32K')
})

// Fix the numeric locale for English accessibility fixtures on every host OS.
beforeEach(()=>{vi.spyOn(Number.prototype,'toLocaleString').mockImplementation(function(locales,options){
  return new Intl.NumberFormat(locales || 'en-US',options).format(this.valueOf())
})})
