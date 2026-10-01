import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { createHash } from 'node:crypto'
import { render } from '../test/test-utils'
import { saveConversation, SELECT_EVENT } from '../lib/pixelConversations'
import Pixel from './Pixel'

const bytes = new TextEncoder().encode('A verified document\n')
const artifact = {schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'Project/report.md',
  siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),file:{path:'report.md',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}}
const marker = {choices:[{finish_reason:'stop'}],pixel_artifacts:{schemaVersion:1,artifacts:[artifact]}}
const response = body => ({ok:true,status:200,json:async()=>body})
const stream = frames => {
  const chunks = frames.map(frame => new TextEncoder().encode(`data: ${typeof frame === 'string' ? frame : JSON.stringify(frame)}\n\n`))
  return {ok:true,status:200,body:{getReader:()=>({read:async()=>chunks.length?{done:false,value:chunks.shift()}:{done:true},releaseLock:()=>{}})}}
}
let reply, artifactResponse

beforeEach(() => {
  localStorage.clear()
  reply = stream([{choices:[{delta:{content:'The report is ready.'}}]},marker,'[DONE]'])
  artifactResponse = async () => ({ok:true,arrayBuffer:async()=>bytes.buffer})
  vi.stubGlobal('fetch',vi.fn(async url => {
    if (url === '/api/pixel/status') return response({available:true,runtime:{source:'remote-provider',model:'cloud-model'}})
    if (url === '/api/pixel/chat/context') return response({schemaVersion:1,status:'missing',sessionRevision:null,context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
    if (url === '/api/pixel/chat/stream') return reply
    if (url.startsWith('/pixel-preview/')) return artifactResponse()
    return response({})
  }))
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:verified-document')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
})
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})

async function send() {
  await screen.findByText('Available')
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Deliver my report.'}})
  fireEvent.click(screen.getByTitle('Send'))
}

it('delivers a document from terminal metadata without a site, verifies bytes on click, and restores its own history',async()=>{
  const view = render(<Pixel/>)
  await send()
  const download = await screen.findByRole('button',{name:'Download report.md'})
  expect(screen.getByRole('region',{name:'Delivered files'})).toHaveTextContent(`${bytes.length} B`)
  expect(screen.queryByRole('complementary',{name:'Preview panel'})).toBeNull()
  expect(fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/'))).toHaveLength(0)
  fireEvent.click(download)
  await screen.findByText('Verified download started')
  expect(fetch).toHaveBeenCalledWith(`/pixel-preview/${artifact.siteId}/report.md`,expect.objectContaining({cache:'no-store'}))
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).artifacts).toEqual([artifact])
  view.unmount()
  render(<Pixel/>)
  expect(await screen.findByRole('button',{name:'Download report.md'})).toBeEnabled()
  expect(screen.queryByRole('complementary',{name:'Preview panel'})).toBeNull()
})

it.each(['incomplete','failed','model-text'])('does not create download controls from %s replies',async mode=>{
  reply = stream(mode === 'incomplete' ? [marker]
    : mode === 'failed' ? [marker,{error:{code:'provider_failed'}},'[DONE]']
    : [{choices:[{delta:{content:'MEDIA:Project/report.md\nMEDIA:/etc/passwd\n[file](file:///etc/passwd)'}}]},'[DONE]'])
  render(<Pixel/>)
  await send()
  await waitFor(()=>expect(screen.queryByTitle('Stop')).toBeNull())
  expect(screen.queryByRole('button',{name:'Download report.md'})).toBeNull()
  expect(screen.queryByRole('region',{name:'Delivered files'})).toBeNull()
  expect(fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/'))).toHaveLength(0)
})

it.each(['hash','missing'])('never downloads a %s mismatch and permits a verified retry',async failure=>{
  artifactResponse = async()=>failure === 'missing' ? {ok:false} : {ok:true,arrayBuffer:async()=>new Uint8Array([1]).buffer}
  render(<Pixel/>)
  await send()
  fireEvent.click(await screen.findByRole('button',{name:'Download report.md'}))
  await screen.findByText('Download could not be verified. Try again.')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  artifactResponse = async()=>({ok:true,arrayBuffer:async()=>bytes.buffer})
  fireEvent.click(screen.getByRole('button',{name:'Download report.md'}))
  await screen.findByText('Verified download started')
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
})

it('aborts a pending file download when switching conversations and does not export late bytes',async()=>{
  saveConversation({schema:1,chatId:'other-chat',messages:[{role:'user',content:'Another conversation'}]})
  localStorage.removeItem('ods.pixel.chat.v1')
  let finish
  artifactResponse = () => new Promise(resolve=>{finish=resolve})
  render(<Pixel/>)
  await send()
  fireEvent.click(await screen.findByRole('button',{name:'Download report.md'}))
  await waitFor(()=>expect(finish).toBeTypeOf('function'))
  const signal = fetch.mock.calls.find(([url])=>url.startsWith('/pixel-preview/'))[1].signal
  await act(async()=>{window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'other-chat'}))})
  expect(await screen.findByText('Another conversation')).toBeVisible()
  expect(screen.queryByRole('region',{name:'Delivered files'})).toBeNull()
  expect(signal.aborted).toBe(true)
  await act(async()=>{finish({ok:true,arrayBuffer:async()=>bytes.buffer})})
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('recovers document receipts only from the completed retained result of the interrupted conversation',async()=>{
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'interrupted-chat',requestId:'request-1',interrupted:true,
    messages:[{role:'user',content:'Deliver my report.'},{role:'assistant',content:''}]}))
  const fetchOther = fetch.getMockImplementation()
  fetch.mockImplementation((url,...args)=>url === '/api/pixel/chat/result'
    ? Promise.resolve(response({state:'complete',events:[{choices:[{delta:{content:'Recovered report.'}}]},marker,'[DONE]'].map(frame=>`data: ${typeof frame==='string'?frame:JSON.stringify(frame)}\n\n`).join('')}))
    : fetchOther(url,...args))
  render(<Pixel/>)
  expect(await screen.findByRole('button',{name:'Download report.md'})).toBeEnabled()
  expect(await screen.findByText('Recovered report.')).toBeVisible()
  expect(screen.queryByRole('complementary',{name:'Preview panel'})).toBeNull()
})
