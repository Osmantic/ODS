import { createHash } from 'node:crypto'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Pixel from './Pixel'
import { inspectZipArchive, readZipTextEntries } from '../lib/pixelZipArchive'

vi.mock('../lib/pixelZipArchive', () => ({inspectZipArchive: vi.fn(), readZipTextEntries: vi.fn()}))

const original = 'AÇÃO-ç-9841\r\nlinha dois\rfinal 😀\n'
const originalBytes = new TextEncoder().encode(original)
const originalHash = createHash('sha256').update(originalBytes).digest('hex')
const archive = {name:'utf8.zip',sha256:'a'.repeat(64),compressedBytes:200,
  entries:[{path:'nested/README.md',kind:'text',bytes:originalBytes.length}]}
const response = body => ({ok:true,json:async()=>body})
let sent
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear(); sent = []
  inspectZipArchive.mockResolvedValue(archive)
  readZipTextEntries.mockResolvedValue({...archive,files:[{path:'nested/README.md',bytes:originalBytes.length,sha256:originalHash,text:original}]})
  vi.stubGlobal('fetch',vi.fn(async (url, options) => {
    if (url === '/api/pixel/chat/context') return response({schemaVersion:1,status:'missing',sessionRevision:null,context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
    if (url === '/api/pixel/chat/stream') {
      sent.push(JSON.parse(options.body))
      let done = false
      return {ok:true,status:200,headers:new Map([['content-type','text/event-stream']]),body:{getReader:()=>({read:async()=> {
        if(done)return {done:true};done=true
        return {done:false,value:new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Received."},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')}
      },releaseLock(){}})}}
    }
    return response({available:true,model:'pixel/default'})
  }))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})

it.each([false,true])('sends the reviewed ZIP text faithfully through the real composer (edit afterwards: %s)',async edit => {
  render(<Pixel/>);await screen.findByText('Available')
  const field=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(field,{target:{value:'Review this:'}})
  const picker=screen.getByLabelText('Choose text file')
  const openPicker=vi.spyOn(picker,'click')
  fireEvent.click(screen.getByRole('button',{name:'Add text file'}))
  expect(openPicker).toHaveBeenCalledOnce()
  expect(picker).toHaveAttribute('type','file')
  expect(picker.accept.split(',')).toContain('.zip')
  fireEvent.change(picker,{target:{files:[new File(['fixture'],'utf8.zip')]}})
  await screen.findByRole('dialog',{name:'utf8.zip'})
  fireEvent.click(screen.getByLabelText('Include nested/README.md'))
  fireEvent.click(screen.getByRole('button',{name:'Review selected text'}))
  const reviewed=(await screen.findByLabelText('Exact ZIP text to insert')).textContent
  expect(sent).toHaveLength(0)
  fireEvent.click(screen.getByRole('button',{name:'Insert selected text'}))
  const visible=field.value
  expect(visible).toBe('Review this: '+reviewed)
  expect(reviewed).toContain('AÇÃO-ç-9841\nlinha dois\nfinal 😀\n')
  expect(reviewed).not.toContain('\r')
  expect(reviewed).toContain(`Original member bytes: ${originalBytes.length}`)
  expect(reviewed).toContain(`Member SHA-256: ${originalHash}`)
  expect(reviewed).toContain('Line endings are normalized to LF for the message; member sizes and hashes describe the original archive bytes.')
  expect(original).toContain('\r\n')
  expect(sent).toHaveLength(0)
  if(edit)fireEvent.change(field,{target:{value:visible+'One more question.'}})
  const expected=field.value.trim()
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(sent).toHaveLength(1))
  expect(sent[0].messages.at(-1).content).toBe(expected)
  expect(sent[0].messages.at(-1).content).toContain(original.replace(/\r\n?/g,'\n'))
})

it.each([false,true])('sends reviewed plain UTF-8 file text faithfully through FileReader (edit afterwards: %s)',async edit => {
  render(<Pixel/>);await screen.findByText('Available')
  const field=screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(field,{target:{value:'Read the file:'}})
  fireEvent.change(screen.getByLabelText('Choose text file'),{target:{files:[new File([originalBytes],'note.md',{type:'text/markdown'})]}})
  const review=await screen.findByRole('group',{name:'Review text file'})
  expect(inspectZipArchive).not.toHaveBeenCalled()
  expect(readZipTextEntries).not.toHaveBeenCalled()
  expect(sent).toHaveLength(0)
  fireEvent.click(screen.getByRole('button',{name:'Insert file text'}))
  if(edit)fireEvent.change(field,{target:{value:field.value+'A follow-up.'}})
  const expected=field.value.trim()
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(sent).toHaveLength(1))
  expect(sent[0].messages.at(-1).content).toBe(expected)
  expect(sent[0].messages.at(-1).content).toContain(original.replace(/\r\n?/g,'\n'))
  expect(sent[0].messages.at(-1).content).not.toContain('\r')
  expect(review.textContent).toContain('Line endings are normalized for the message; the original file is unchanged.')
  expect(original).toContain('\r\n')
})
