import {fireEvent,render,screen,waitFor} from '@testing-library/react'
import ODSTalk from './ODSTalk'

afterEach(() => vi.unstubAllGlobals())
async function start(frames, holdOpen=false) {
  let read=false
  const reader={read:vi.fn(async () => {
    if(!read){read=true;return {value:new TextEncoder().encode(frames.map(frame=>`data: ${JSON.stringify(frame)}\n\n`).join('')),done:false}}
    return holdOpen ? new Promise(()=>{}) : {done:true}
  }),cancel:vi.fn(async()=>{}),releaseLock:vi.fn()}
  vi.stubGlobal('fetch',vi.fn(async url => url==='/api/talk/status'
    ? {ok:true,json:async()=>({capabilities:{text_chat:true}})}
    : {ok:true,body:{getReader:()=>reader}}))
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Explain'}})
  fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  return reader
}
it('keeps partial text but reports EOF without completion as interrupted',async()=>{
  await start([{type:'delta',text:'Partial answer'}])
  await screen.findByRole('button',{name:'Retry last message'})
  expect(screen.getByText('Partial answer')).toBeInTheDocument()
  expect(screen.getByText(/ended before completion/i)).toBeInTheDocument()
})
it('settles on done without waiting for a proxy to close the body',async()=>{
  const reader=await start([{type:'complete',text:'Final answer'},{type:'done'}],true)
  await screen.findByText('Final answer')
  await waitFor(()=>expect(reader.cancel).toHaveBeenCalledOnce())
  expect(reader.releaseLock).toHaveBeenCalledOnce()
  expect(screen.queryByRole('button',{name:'Retry last message'})).not.toBeInTheDocument()
})
it('retains streamed text next to the terminal server error',async()=>{
  await start([{type:'delta',text:'Useful partial'},{type:'error',detail:'Worker stopped'},{type:'done'}])
  await screen.findByRole('button',{name:'Retry last message'})
  expect(screen.getByText('Useful partial')).toBeInTheDocument()
  expect(screen.getByText('Worker stopped')).toBeInTheDocument()
})
