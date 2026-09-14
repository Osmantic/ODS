import {act,fireEvent,screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import ODSTalk from './ODSTalk'
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
it('pauses automatic scrolling while reading earlier content and resumes on demand',async()=>{
  let emit
  vi.stubGlobal('fetch',vi.fn(async url=>url==='/api/talk/status'?{ok:true,json:async()=>({capabilities:{text_chat:true}})}:{ok:true,body:{getReader:()=>({read:()=>new Promise(resolve=>{emit=text=>resolve({done:false,value:new TextEncoder().encode(`data: ${JSON.stringify({type:'delta',text})}\n\n`)})}),cancel:async()=>{},releaseLock:()=>{}})}}))
  const scroll=vi.fn()
  Object.defineProperty(globalThis.Element.prototype,'scrollIntoView',{configurable:true,value:scroll})
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Long answer'}})
  fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  await act(async()=>{})
  await act(async()=>emit('First part'))
  Object.defineProperty(document.documentElement,'scrollHeight',{configurable:true,value:3000})
  Object.defineProperty(window,'innerHeight',{configurable:true,value:800})
  Object.defineProperty(window,'scrollY',{configurable:true,value:300})
  fireEvent.scroll(window)
  scroll.mockClear()
  await act(async()=>emit(' second part'))
  expect(scroll).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Jump to latest reply'}))
  expect(scroll).toHaveBeenCalledOnce()
  scroll.mockClear()
  await act(async()=>emit(' third part'))
  expect(scroll).toHaveBeenCalledOnce()
})
