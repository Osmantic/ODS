import {fireEvent,screen,waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import ODSTalk from './ODSTalk'
afterEach(()=>vi.unstubAllGlobals())
it('stops a live response, retains received text and permits another message',async()=>{
  let signal,readCount=0
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{
    if(url==='/api/talk/status')return {ok:true,json:async()=>({capabilities:{text_chat:true}})}
    signal=options.signal
    return {ok:true,body:{getReader:()=>({cancel:async()=>{},releaseLock:()=>{},read:async()=>{
      if(readCount++===0)return {done:false,value:new TextEncoder().encode('data: {"type":"delta","text":"Useful partial reply"}\n\n')}
      return new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new globalThis.DOMException('Stopped','AbortError')),{once:true}))
    }})}}
  }))
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Explain'}})
  fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  await screen.findByText('Useful partial reply')
  fireEvent.click(screen.getByRole('button',{name:'Stop response'}))
  await screen.findByText('Response stopped. The reply may be incomplete.')
  expect(signal.aborted).toBe(true)
  expect(screen.getByText('Useful partial reply')).toBeVisible()
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Next'}})
  await waitFor(()=>expect(screen.getByRole('button',{name:'Send message'})).toBeEnabled())
})

