import {fireEvent,screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import ODSTalk from './ODSTalk'
const reply='Run **diagnostics** with `ods doctor`.\n\n```sh\nods status\n```'
beforeEach(()=>{
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockResolvedValue()}})
  const frames=[{type:'complete',text:reply,status:'ok'},{type:'done'}]
  vi.stubGlobal('fetch',vi.fn(async url=>url==='/api/talk/status'?{ok:true,json:async()=>({capabilities:{text_chat:true}})}:{ok:true,body:{getReader:()=>({read:async()=>frames.length?{done:false,value:new TextEncoder().encode(`data: ${JSON.stringify(frames.shift())}\n\n`)}:{done:true},cancel:async()=>{},releaseLock:()=>{}})}}))
})
afterEach(()=>vi.unstubAllGlobals())
async function answer(){render(<ODSTalk/>);await screen.findByText('Ready');fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Help'}});fireEvent.click(screen.getByRole('button',{name:'Send message'}));return screen.findByRole('button',{name:'Copy reply'})}
it('copies original Markdown rather than flattened rendered text',async()=>{
  fireEvent.click(await answer())
  await screen.findByText('Reply copied.')
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(reply)
})
it('offers the exact source for manual copying when clipboard permission is denied',async()=>{
  navigator.clipboard.writeText.mockRejectedValue(new Error('denied'))
  fireEvent.click(await answer())
  expect(await screen.findByRole('alert')).toHaveTextContent('Clipboard unavailable')
  expect(screen.getByLabelText('Reply Markdown for manual copying')).toHaveValue(reply)
  expect(screen.queryByText('Reply copied.')).toBeNull()
})
