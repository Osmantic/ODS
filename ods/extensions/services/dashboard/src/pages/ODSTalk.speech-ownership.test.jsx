import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import ODSTalk from './ODSTalk'

const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r}),resolve:value=>resolve(value)}}
let audio
beforeEach(()=>{
  localStorage.clear()
  audio={play:vi.fn(async()=>{}),pause:vi.fn(),addEventListener:vi.fn(),paused:false}
  vi.stubGlobal('Audio',class {constructor(){return audio}})
  vi.stubGlobal('MediaSource',undefined)
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:speech')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();localStorage.clear()})
async function start(speech){
  vi.stubGlobal('fetch',vi.fn(url=>{
    if(url==='/api/talk/status') return Promise.resolve({ok:true,json:async()=>({capabilities:{text_chat:true,tts:true}})})
    if(url==='/api/talk/speak') return speech.promise
    let read=false
    return Promise.resolve({ok:true,body:{getReader:()=>({cancel:async()=>{},releaseLock:()=>{},read:async()=>read?{done:true}:(read=true,{done:false,value:new TextEncoder().encode('data: {"type":"complete","text":"Spoken answer"}\n\ndata: {"type":"done"}\n\n')})})}})
  }))
  const view=render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.click(screen.getByRole('button',{name:'Turn spoken replies on'}))
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Speak'}})
  fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/talk/speak',expect.any(Object)))
  return view
}
it('turning speech off aborts pending synthesis and rejects a late response',async()=>{
  const speech=deferred();await start(speech)
  const options=fetch.mock.calls.find(([url])=>url==='/api/talk/speak')[1]
  fireEvent.click(screen.getByRole('button',{name:'Turn spoken replies off'}))
  const cancel=vi.fn(async()=>{})
  await act(async()=>speech.resolve({ok:true,body:{cancel},blob:async()=>new Blob(['audio'])}))
  expect(options.signal?.aborted).toBe(true)
  expect(audio.play).not.toHaveBeenCalled()
  expect(cancel).toHaveBeenCalledOnce()
})
it('does not play a late iOS-style blob after unmount',async()=>{
  const speech=deferred(),body=deferred();const view=await start(speech)
  await act(async()=>speech.resolve({ok:true,body:{},blob:()=>body.promise}))
  view.unmount()
  await act(async()=>body.resolve(new Blob(['audio'])))
  expect(audio.play).not.toHaveBeenCalled()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
