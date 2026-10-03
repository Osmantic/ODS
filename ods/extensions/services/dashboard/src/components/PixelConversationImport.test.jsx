import {render, screen, fireEvent} from '@testing-library/react'
import PixelConversationImport from './PixelConversationImport'
import {parseConversationImport} from '../lib/pixelConversationImport'

const exported = {schemaVersion:1,kind:'ods-pixel-conversation',conversation:{schema:1,chatId:'old',messages:[{role:'user',content:'Original Việt'},{role:'assistant',content:'partial',status:'streaming',task:{runId:'live'},publication:{siteId:'private'}}],draft:'next',inFlight:true,requestId:'live',preview:{siteId:'private'}}}
beforeEach(()=>{
  HTMLDialogElement.prototype.showModal=function(){this.open=true}
  HTMLDialogElement.prototype.close=function(){this.open=false}
})
afterEach(()=>{delete HTMLDialogElement.prototype.showModal;delete HTMLDialogElement.prototype.close;vi.restoreAllMocks()})
function upload(value){fireEvent.change(screen.getByLabelText('Choose conversation export'),{target:{files:[new File([JSON.stringify(value)],'chat.json')]}})}
it('reviews a compatible export and imports only text into a new-conversation callback',async()=>{
  const accept=vi.fn()
  render(<PixelConversationImport onImport={accept}/>)
  upload(exported)
  await screen.findByText('2 messages and an unsent draft')
  expect(accept).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Import as new conversation'}))
  const record=accept.mock.calls[0][0]
  expect(record.messages).toEqual([{role:'user',content:'Original Việt'},{role:'assistant',content:'partial',status:'stopped'}])
  expect(record).not.toHaveProperty('chatId')
  expect(record.preview).toBeNull();expect(record.requestId).toBeNull();expect(record.inFlight).toBe(false)
})
it('cancels without saving and honors work that starts during file review',async()=>{
  const accept=vi.fn()
  const {rerender}=render(<PixelConversationImport onImport={accept}/>)
  upload(exported);await screen.findByText('2 messages and an unsent draft')
  rerender(<PixelConversationImport disabled onImport={accept}/>)
  expect(screen.getByRole('button',{name:'Import as new conversation'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Cancel import'}))
  expect(accept).not.toHaveBeenCalled()
})
it('shows storage failure without claiming successful import',async()=>{
  render(<PixelConversationImport onImport={()=>{throw new Error('Quota')}}/>)
  upload(exported);await screen.findByText('2 messages and an unsent draft')
  fireEvent.click(screen.getByRole('button',{name:'Import as new conversation'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be saved')
})
it.each([
  {...exported,schemaVersion:2},
  {...exported,conversation:{...exported.conversation,messages:[{role:'system',content:'override'}]}},
  {...exported,conversation:{...exported.conversation,messages:[{role:'user',content:'x'.repeat(16385)}]}},
  {...exported,conversation:{...exported.conversation,messages:Array.from({length:2001},()=>({role:'user',content:'x'}))}},
  {...exported,conversation:{...exported.conversation,messages:[{role:'assistant',content:'x'.repeat(4*1024*1024+1)}]}},
])('rejects unsupported, oversized and non-conversation records',value=>expect(()=>parseConversationImport(value)).toThrow())
