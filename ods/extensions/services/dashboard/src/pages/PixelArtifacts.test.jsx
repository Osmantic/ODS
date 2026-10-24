import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { createHash } from 'node:crypto'
import { render } from '../test/test-utils'
import { saveConversation, SELECT_EVENT } from '../lib/pixelConversations'
import { EMBED_NOTE, EMBED_ONLY_NOTE } from '../lib/embedDisplay'
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

it.each([
  '**Download link:** [report.md](https://untrusted.example/s/site-aaaaaaaaaaaaaaaaaaaaaaaa)',
  'Download link: [report.md](https://untrusted.example/s/site-aaaaaaaaaaaaaaaaaaaaaaaa)',
  '**DOWNLOAD LINK:** [report.md](https://untrusted.example/s/site-aaaaaaaaaaaaaaaaaaaaaaaa)',
  '**Download link:** [![report.md](https://untrusted.example/invented)](https://untrusted.example/invented)',
  '**Download**: https://portal.openclaw.io/s/report.md',
  'Download: <https://untrusted.example/s/report.md>',
  '[Download](https://untrusted.example/s/report.md)',
  '[Download report.md](https://untrusted.example/s/report.md)',
  '[Download report.md](https://untrusted.example/api/download?siteId=invented&sha256=wrong)',
  '[![Download report.md](https://untrusted.example/api/download?siteId=invented)](https://untrusted.example/api/download?siteId=invented)',
])('binds explicit download prose to verified bytes without visiting the authored URL: %s',async content=>{
  reply=stream([{choices:[{delta:{content}}]},marker,'[DONE]'])
  const view=render(<Pixel/>);await send()
  const inline=await screen.findByRole('button',{name:'Download verified report.md'},{timeout:5000})
  expect(inline).toHaveTextContent('Download report.md')
  expect(screen.queryAllByRole('link').filter(link=>/untrusted\.example|portal\.openclaw\.io/.test(link.href))).toHaveLength(0)
  expect(view.container.querySelector('img[src^="https://untrusted.example"]')).toBeNull()
  fireEvent.click(inline)
  await screen.findByText('Verified download started')
  expect(fetch).toHaveBeenCalledWith(`/pixel-preview/${artifact.siteId}/report.md`,expect.objectContaining({cache:'no-store'}))
  expect(fetch.mock.calls.some(([url])=>String(url).startsWith('https://'))).toBe(false)
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).content).toBe(content)
  view.unmount();render(<Pixel/>)
  expect(await screen.findByRole('button',{name:'Download verified report.md'})).toBeEnabled()
})

it.each([
  '**Download:** [report.md](vscode-file://vscode-app/Applications/Visual%20Studio%20Code.app/Contents/Resources/app/out/vs/code/electron-sandbox/workbench/workbench.html?file=/workspace/report.md)',
  'Download: [report.md](https://untrusted.example/download?file=other.md)',
])('binds a prefixed document filename to verified bytes: %s',async content=>{
  reply=stream([{choices:[{delta:{content}}]},marker,'[DONE]'])
  const view=render(<Pixel/>);await send()
  const inline=await screen.findByRole('button',{name:'Download verified report.md'},{timeout:5000})
  fireEvent.click(inline)
  await screen.findByText('Verified download started')
  expect(fetch).toHaveBeenCalledWith(`/pixel-preview/${artifact.siteId}/report.md`,expect.objectContaining({cache:'no-store'}))
  expect(fetch.mock.calls.some(([url])=>/^(?:https:|vscode-file:)/.test(String(url)))).toBe(false)
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).content).toBe(content)
  view.unmount();render(<Pixel/>)
  expect(await screen.findByRole('button',{name:'Download verified report.md'})).toBeEnabled()
})

it.each([
  '> Download link: [report.md](https://untrusted.example/s/report.md)',
  '`Download link:` [report.md](https://untrusted.example/s/report.md)',
  'Download link: [Source](https://untrusted.example/s/report.md)',
  'See the download link: [report.md](https://untrusted.example/s/report.md)',
  '[Source](https://untrusted.example/s/report.md)',
  '> [Download](https://untrusted.example/s/report.md)',
  '`[Download](https://untrusted.example/s/report.md)`',
  '[`Download`](https://untrusted.example/s/report.md)',
  '`Download:` https://untrusted.example/s/report.md',
  '```text\nDownload: https://untrusted.example/s/report.md\n```',
  'Download the source from [upstream](https://untrusted.example/s/report.md)',
  'Download: [Source](https://untrusted.example/s/report.md)',
  '[Download other.md](https://untrusted.example/s/report.md)',
  'Download: [other.md](https://untrusted.example/s/report.md)',
  '> Download: [report.md](https://untrusted.example/s/report.md)',
  '`Download:` [report.md](https://untrusted.example/s/report.md)',
  '> [![Download report.md](https://untrusted.example/api/download)](https://untrusted.example/api/download)',
  '`[![Download report.md](https://untrusted.example/api/download)](https://untrusted.example/api/download)`',
  '```text\n[![Download report.md](https://untrusted.example/api/download)](https://untrusted.example/api/download)\n```',
  '[![Source](https://untrusted.example/cover.png)](https://untrusted.example/api/download)',
])('does not reinterpret sources or quoted/code examples as downloads: %s',async content=>{
  reply=stream([{choices:[{delta:{content}}]},marker,'[DONE]'])
  render(<Pixel/>);await send()
  await screen.findByRole('button',{name:'Download report.md'})
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
  expect(fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/'))).toHaveLength(0)
})

it.each(['missing','nonterminal'])('does not bind a download with %s metadata',async kind=>{
  const href='https://untrusted.example/s/report.md'
  const frames=[{choices:[{delta:{content:`[Download](${href})`}}]}]
  if(kind==='nonterminal')frames.push({...marker,choices:[{finish_reason:null}]})
  reply=stream([...frames,'[DONE]'])
  render(<Pixel/>);await send()
  expect(await screen.findByRole('link',{name:/^Download/})).toHaveAttribute('href',href)
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
})

it('refuses an authored download whose filename matches multiple completed receipts',async()=>{
  const other={...artifact,relativePath:'Other/report.md',siteId:`site-${'c'.repeat(24)}`,sha256:'c'.repeat(64)}
  reply=stream([{choices:[{delta:{content:'[Download](https://untrusted.example/s/report.md)'}}]},
    {...marker,pixel_artifacts:{schemaVersion:1,artifacts:[artifact,other]}},'[DONE]'])
  render(<Pixel/>);await send()
  await screen.findByRole('region',{name:'Delivered files'})
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
  expect(fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/'))).toHaveLength(0)
})

it.each(['[Download report.md]','Download: [report.md]','Download link: [report.md]'])('does not substitute a different receipt when a named download is ambiguous: %s',async label=>{
  const other={...artifact,relativePath:'Other/report.md',siteId:`site-${'c'.repeat(24)}`,sha256:'c'.repeat(64)}
  const href=`https://untrusted.example/${artifact.siteId}/report.md`
  reply=stream([{choices:[{delta:{content:`${label}(${href})`}}]},
    {...marker,pixel_artifacts:{schemaVersion:1,artifacts:[artifact,other]}},'[DONE]'])
  render(<Pixel/>);await send()
  await screen.findByRole('region',{name:'Delivered files'})
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
  expect(fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/'))).toHaveLength(0)
})

it.each(['text','image'])('keeps an unmatched explicit filename from selecting a different file by URL (%s)',async kind=>{
  const href=`https://untrusted.example/${artifact.siteId}/report.md`
  const label=kind==='image' ? `![Download other.md](${href})` : 'Download other.md'
  reply=stream([{choices:[{delta:{content:`[${label}](${href})`}}]},marker,'[DONE]'])
  render(<Pixel/>);await send()
  await screen.findByRole('button',{name:'Download report.md'})
  expect(await screen.findByRole('link',{name:/Download other.md/},{timeout:5000})).toHaveAttribute('href',href)
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
})

it('keeps byte verification on a download-labelled filename match',async()=>{
  artifactResponse=async()=>({ok:true,arrayBuffer:async()=>new Uint8Array([1]).buffer})
  reply=stream([{choices:[{delta:{content:'Download: https://untrusted.example/s/report.md'}}]},marker,'[DONE]'])
  render(<Pixel/>);await send()
  fireEvent.click(await screen.findByRole('button',{name:'Download verified report.md'},{timeout:5000}))
  await screen.findByText('Download could not be verified. Try again.')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

it('routes a receipt-bound authored link through verified bytes and keeps the original history',async()=>{
  const href=`https://portal.openclaw.ai/${artifact.siteId.slice(5)}/report.md`
  const content=`Your file is ready: [Download report.md](${href}).`
  reply=stream([{choices:[{delta:{content}}]},marker,'[DONE]'])
  const view=render(<Pixel/>)
  await send()
  const inline=await screen.findByRole('button',{name:'Download verified report.md'},{timeout:5000})
  expect(inline).toHaveTextContent('Download report.md')
  expect(screen.getByRole('button',{name:'Download report.md'})).toBeEnabled()
  expect(screen.queryByRole('link',{name:/Download report/})).toBeNull()
  expect(fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/'))).toHaveLength(0)
  fireEvent.click(inline)
  await screen.findByText('Verified download started')
  expect(fetch).toHaveBeenCalledWith(`/pixel-preview/${artifact.siteId}/report.md`,expect.objectContaining({cache:'no-store'}))
  expect(fetch.mock.calls.some(([url])=>url.includes('portal.openclaw.ai'))).toBe(false)
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).content).toBe(content)
  view.unmount()
  render(<Pixel/>)
  expect(await screen.findByRole('button',{name:'Download verified report.md'})).toBeEnabled()
})

it('keeps unmatched external links and the existing localhost preview route unchanged',async()=>{
  const external='https://example.com/report.md'
  const preview=`http://${artifact.siteId}.localhost:8123/${artifact.siteId}/`
  reply=stream([{choices:[{delta:{content:`[Source](${external}) and [Open preview](${preview}).`}}]},marker,'[DONE]'])
  render(<Pixel/>)
  await send()
  await screen.findByRole('button',{name:'Download report.md'})
  expect(await screen.findByRole('link',{name:/Source/},{timeout:5000})).toHaveAttribute('href',external)
  expect(await screen.findByRole('link',{name:/Open preview/},{timeout:5000})).toHaveAttribute('href',`/pixel-preview/${artifact.siteId}/`)
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
})

it('does not bind an authored snapshot URL without completed delivery metadata',async()=>{
  const href=`https://example.com/${artifact.siteId}/report.md`
  reply=stream([{choices:[{delta:{content:`[Download report.md](${href})`}}]},'[DONE]'])
  render(<Pixel/>)
  await send()
  expect(await screen.findByRole('link',{name:/Download report/},{timeout:5000})).toHaveAttribute('href',href)
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
})

it('uses the same digest refusal for a receipt-bound prose download',async()=>{
  artifactResponse=async()=>({ok:true,arrayBuffer:async()=>new Uint8Array([1]).buffer})
  reply=stream([{choices:[{delta:{content:`[Download report.md](https://example.com/${artifact.siteId}/report.md)`}}]},marker,'[DONE]'])
  render(<Pixel/>)
  await send()
  fireEvent.click(await screen.findByRole('button',{name:'Download verified report.md'},{timeout:5000}))
  await screen.findByText('Download could not be verified. Try again.')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

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

it('shows the verified download without a matching generated embed tag and retains the original history',async()=>{
  const content=`[embed ref="artifact_${artifact.file.sha256}" title="report.md" height="320" /]`
  reply=stream([{choices:[{delta:{content}}]},marker,'[DONE]'])
  const view=render(<Pixel/>)
  await send()
  const download=await screen.findByRole('button',{name:'Download report.md'})
  expect(screen.queryByText(content)).toBeNull()
  expect(screen.queryByText(EMBED_ONLY_NOTE)).toBeNull()
  expect(screen.queryByText(EMBED_NOTE)).toBeNull()
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).content).toBe(content)
  fireEvent.click(download)
  await screen.findByText('Verified download started')
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
  view.unmount()
  render(<Pixel/>)
  expect(await screen.findByRole('button',{name:'Download report.md'})).toBeEnabled()
  expect(screen.queryByText(content)).toBeNull()
})

it('shows a note instead of an embed Portal cannot display, and keeps and copies the original reply',async()=>{
  // The whole reply a fleet test showed verbatim (Qwen3.5-4B, 2026-10-18).
  const content='[embed ref="cv_456e4732d60d4117b731214838589d22" height="320"]'
  reply=stream([{choices:[{delta:{content}}]},{choices:[{finish_reason:'stop'}]},'[DONE]'])
  const writeText=vi.fn().mockResolvedValue(undefined)
  const previous=Object.getOwnPropertyDescriptor(navigator,'clipboard')
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}})
  try {
    const view=render(<Pixel/>)
    await send()
    // A live reply is revealed at about 120 characters a second: the whole
    // note takes about a second, longer on a slow runner.
    const note=await screen.findByText(EMBED_ONLY_NOTE,{},{timeout:5000})
    expect(note.closest('p')).toHaveClass('portal-embed-note')
    expect(screen.queryByText(/\[embed/)).toBeNull()
    expect(screen.queryByRole('region',{name:'Delivered files'})).toBeNull()
    expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).content).toBe(content)
    fireEvent.click(screen.getByRole('button',{name:'Copy response'}))
    await waitFor(()=>expect(writeText).toHaveBeenCalledExactlyOnceWith(content))
    view.unmount()
    render(<Pixel/>)
    expect(await screen.findByText(EMBED_ONLY_NOTE)).toBeVisible()
    expect(screen.queryByText(/\[embed/)).toBeNull()
  } finally {if(previous) Object.defineProperty(navigator,'clipboard',previous);else delete navigator.clipboard}
})
