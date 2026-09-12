import {fireEvent,render,screen,waitFor} from '@testing-library/react'
import ODSTalk from './ODSTalk'

afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
it('retries the original recorded file through the audio endpoint',async()=>{
  vi.stubGlobal('isSecureContext',true)
  vi.stubGlobal('navigator',{mediaDevices:{getUserMedia:vi.fn(async()=>({getTracks:()=>[{stop:vi.fn()}]}))}})
  vi.stubGlobal('MediaRecorder',class {
    state='inactive';mimeType='audio/webm'
    start(){this.state='recording'}
    stop(){this.state='inactive';this.ondataavailable({data:new Blob(['original voice'],{type:this.mimeType})});this.onstop()}
  })
  let requests=0
  vi.stubGlobal('fetch',vi.fn(async url=>{
    if(url==='/api/talk/status')return {ok:true,json:async()=>({capabilities:{text_chat:true,audio_message:true}})}
    if(url==='/api/talk/audio-message')return ++requests===1 ? {ok:false,status:503,json:async()=>({detail:'Speech service offline'})} : {ok:true,json:async()=>({transcript:'My original words',text:'Recovered reply'})}
    return {ok:false,status:400,json:async()=>({detail:'Wrong retry endpoint'})}
  }))
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.click(screen.getByRole('button',{name:'Record voice'}))
  fireEvent.click(await screen.findByRole('button',{name:'Stop recording'}))
  await screen.findByText('Speech service offline')
  fireEvent.click(screen.getByRole('button',{name:'Retry last message'}))
  await waitFor(()=>expect(requests).toBe(2))
  const sends=fetch.mock.calls.filter(([url])=>url==='/api/talk/audio-message')
  const retriedFile=sends[1][1].body.get('file')
  expect(retriedFile.name).toBe('recording.webm')
  expect(await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(retriedFile)})).toBe('original voice')
  expect(await screen.findByText('My original words')).toBeInTheDocument()
  expect(fetch.mock.calls.some(([url])=>url==='/api/talk/message/stream')).toBe(false)
})
