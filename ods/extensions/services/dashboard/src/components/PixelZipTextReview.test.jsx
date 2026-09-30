import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import PixelTextFileInput from './PixelTextFileInput'
import { formatZipText } from './PixelZipTextReview'
import { appendComposerText } from '../lib/pixelComposerText'
import { inspectZipArchive, readZipTextEntries } from '../lib/pixelZipArchive'

vi.mock('../lib/pixelZipArchive', () => ({inspectZipArchive: vi.fn(), readZipTextEntries: vi.fn()}))

const props = {input:'Find the issue', limit:16384, disabled:false, conversationId:'first'}
const hash = 'a'.repeat(64), memberHash = 'b'.repeat(64)
const metadata = {name:'project.zip', sha256:hash, compressedBytes:818, entries:[
  {path:'README.md', bytes:10, kind:'text'},
  {path:'data.csv', bytes:40, kind:'text'},
  {path:'logo.png', bytes:25, kind:'unread', reason:'Binary image'},
  {path:'nested.zip', bytes:50, kind:'unread', reason:'Nested archive'},
]}
const member = {path:'README.md', bytes:10, sha256:memberHash, text:'Olá\r\n```\r\n'}
const upload = () => fireEvent.change(screen.getByLabelText('Choose text file'), {target:{files:[new File(['zip'], 'project.zip')]}})
const deferred = () => {let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
beforeEach(() => {
  vi.clearAllMocks()
  inspectZipArchive.mockResolvedValue(metadata)
  readZipTextEntries.mockResolvedValue({...metadata, files:[member]})
})

async function stage(insert=vi.fn()) {
  const view = render(<PixelTextFileInput {...props} onInsert={insert}/>)
  screen.getByRole('button', {name:'Add text file'}).focus()
  upload()
  await screen.findByRole('dialog', {name:'project.zip'})
  return {view,insert}
}
async function review() {
  fireEvent.click(screen.getByLabelText('Include README.md'))
  fireEvent.click(screen.getByRole('button', {name:'Review selected text'}))
  return screen.findByLabelText('Exact ZIP text to insert')
}

it('lists without reading text, then reviews exact selected payload and only inserts on confirmation', async () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch',fetch)
  try {
    const {insert} = await stage()
    expect(readZipTextEntries).not.toHaveBeenCalled()
    expect(screen.getByRole('button', {name:'Review selected text'})).toBeDisabled()
    expect(screen.getByLabelText('Include logo.png')).toBeDisabled()
    expect(screen.getByLabelText('Include nested.zip')).toBeDisabled()
    const preview = await review()
    const exact = formatZipText({...metadata, files:[member]})
    expect(preview.textContent).toBe(exact)
    expect(exact).toContain('Olá\r\n```\r\n')
    expect(exact).toContain('````text')
    expect(exact).toContain(`Archive SHA-256: ${hash}`)
    expect(exact).toContain(`Member SHA-256: ${memberHash}`)
    expect(exact).toContain('"data.csv" (text not selected)')
    expect(exact).toContain('"logo.png" (not read: Binary image)')
    expect(readZipTextEntries.mock.calls[0][1]).toEqual(['README.md'])
    expect(insert).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', {name:'Insert selected text'}))
    expect(insert).toHaveBeenCalledExactlyOnceWith(exact)
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  } finally { vi.unstubAllGlobals() }
})

it('rechecks CURRENT draft and wrapper/coverage budget; never truncates or inserts over limit', async () => {
  const {view,insert} = await stage()
  const preview = await review()
  const exact = preview.textContent
  const length = appendComposerText(props.input,exact).length
  view.rerender(<PixelTextFileInput {...props} limit={length} onInsert={insert}/>)
  expect(screen.getByRole('button',{name:'Insert selected text'})).toBeEnabled()
  view.rerender(<PixelTextFileInput {...props} limit={length-1} onInsert={insert}/>)
  expect(screen.getByRole('button',{name:'Insert selected text'})).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('Nothing was shortened')
  expect(preview.textContent).toBe(exact)
  view.rerender(<PixelTextFileInput {...props} input={'x'.repeat(16384)} onInsert={insert}/>)
  expect(screen.getByRole('button',{name:'Insert selected text'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Insert selected text'}))
  expect(insert).not.toHaveBeenCalled()
  view.rerender(<PixelTextFileInput {...props} disabled onInsert={insert}/>)
  expect(screen.getByRole('button',{name:'Insert selected text'})).toBeDisabled()
})

it('preserves draft on malformed archives and selected-member failures', async () => {
  const insert=vi.fn()
  inspectZipArchive.mockRejectedValueOnce(new Error('Invalid archive structure'))
  render(<PixelTextFileInput {...props} onInsert={insert}/>)
  upload()
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid archive structure')
  expect(insert).not.toHaveBeenCalled()
  upload();await screen.findByRole('dialog')
  readZipTextEntries.mockRejectedValueOnce(new Error('Selected file is not valid UTF-8'))
  fireEvent.click(screen.getByLabelText('Include README.md'))
  fireEvent.click(screen.getByRole('button',{name:'Review selected text'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('not valid UTF-8')
  expect(screen.queryByLabelText('Exact ZIP text to insert')).toBeNull()
  expect(insert).not.toHaveBeenCalled()
})

it.each(['cancel','unmount','conversation'])('discards late archive listing after %s', async how => {
  const pending=deferred();inspectZipArchive.mockReturnValue(pending.promise)
  const insert=vi.fn(),view=render(<PixelTextFileInput {...props} onInsert={insert}/>)
  upload()
  const signal=inspectZipArchive.mock.calls[0][1].signal
  if(how==='cancel')fireEvent.click(screen.getByRole('button',{name:'Cancel reading'}))
  else if(how==='unmount')view.unmount()
  else view.rerender(<PixelTextFileInput {...props} conversationId="other" onInsert={insert}/>)
  expect(signal.aborted).toBe(true)
  await act(async()=>pending.resolve(metadata))
  expect(screen.queryByRole('dialog')).toBeNull();expect(insert).not.toHaveBeenCalled()
})

it.each(['cancel','unmount','conversation'])('discards late selected text after %s', async how => {
  const pending=deferred();readZipTextEntries.mockReturnValue(pending.promise)
  const {view,insert}=await stage()
  fireEvent.click(screen.getByLabelText('Include README.md'))
  fireEvent.click(screen.getByRole('button',{name:'Review selected text'}))
  const signal=readZipTextEntries.mock.calls[0][2].signal
  if(how==='cancel')fireEvent.click(screen.getByRole('button',{name:'Cancel reading'}))
  else if(how==='unmount')view.unmount()
  else view.rerender(<PixelTextFileInput {...props} conversationId="other" onInsert={insert}/>)
  expect(signal.aborted).toBe(true)
  await act(async()=>pending.resolve({...metadata,files:[member]}))
  expect(screen.queryByLabelText('Exact ZIP text to insert')).toBeNull();expect(insert).not.toHaveBeenCalled()
})

it('ignores a previous selection and requires review again after changing files', async()=>{
  await stage();await review()
  fireEvent.click(screen.getByRole('button',{name:'Change selection'}))
  expect(screen.queryByRole('button',{name:'Insert selected text'})).toBeNull()
  fireEvent.click(screen.getByLabelText('Include README.md'))
  expect(screen.getByRole('button',{name:'Review selected text'})).toBeDisabled()
})

it('rejects a worker response with the wrong archive or selected members', async()=>{
  readZipTextEntries.mockResolvedValue({...metadata,sha256:'c'.repeat(64),files:[member]})
  await stage();fireEvent.click(screen.getByLabelText('Include README.md'))
  fireEvent.click(screen.getByRole('button',{name:'Review selected text'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('do not match this archive')
  expect(screen.queryByRole('button',{name:'Insert selected text'})).toBeNull()
})

it('fences adversarial backticks without an argument-limit failure',()=>{
  const text='`x'.repeat(128*1024)
  expect(()=>formatZipText({...metadata,files:[{...member,text}]})).not.toThrow()
  expect(formatZipText({...metadata,files:[{...member,text}]})).toContain(text)
})

it('keeps keyboard focus in the review and Escape discards without insertion',async()=>{
  const {insert}=await stage()
  const dialog=screen.getByRole('dialog'),close=within(dialog).getByRole('button',{name:'Discard ZIP attachment'})
  expect(close).toHaveFocus()
  fireEvent.keyDown(close,{key:'Tab',shiftKey:true})
  expect(screen.getByRole('button',{name:'Cancel'})).toHaveFocus()
  fireEvent.keyDown(dialog,{key:'Escape'})
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull())
  expect(screen.getByRole('button',{name:'Add text file'})).toHaveFocus()
  expect(insert).not.toHaveBeenCalled()
})

it('returns focus to the attachment trigger on Cancel',async()=>{
  await stage()
  const cancel=screen.getByRole('button',{name:'Cancel'})
  cancel.focus();fireEvent.click(cancel)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button',{name:'Add text file'})).toHaveFocus()
})

it('keeps the composer focused after insertion and never steals focus on a conversation change',async()=>{
  const composer=document.createElement('textarea')
  document.body.appendChild(composer)
  try {
    const insert=vi.fn(()=>composer.focus())
    const {view}=await stage(insert)
    await review()
    fireEvent.click(screen.getByRole('button',{name:'Insert selected text'}))
    expect(composer).toHaveFocus()
    upload();await screen.findByRole('dialog')
    composer.focus()
    view.rerender(<PixelTextFileInput {...props} conversationId="new-chat" onInsert={insert}/>)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(composer).toHaveFocus()
    expect(insert).toHaveBeenCalledTimes(1)
  } finally { composer.remove() }
})

it('does not replace a newer archive with a late result from an earlier file choice',async()=>{
  const first=deferred()
  inspectZipArchive.mockReturnValueOnce(first.promise)
  render(<PixelTextFileInput {...props} onInsert={vi.fn()}/>)
  upload()
  const oldSignal=inspectZipArchive.mock.calls[0][1].signal
  inspectZipArchive.mockResolvedValueOnce({...metadata,name:'new.zip'})
  fireEvent.change(screen.getByLabelText('Choose text file'),{target:{files:[new File(['other'],'new.zip')]}})
  await screen.findByRole('dialog',{name:'new.zip'})
  expect(oldSignal.aborted).toBe(true)
  await act(async()=>first.resolve(metadata))
  expect(screen.getByRole('dialog',{name:'new.zip'})).toBeInTheDocument()
  expect(screen.queryByRole('dialog',{name:'project.zip'})).toBeNull()
})

it('uses the composer UTF-16 budget for astral characters without changing the reviewed text',async()=>{
  readZipTextEntries.mockResolvedValueOnce({...metadata,files:[{...member,text:'🪐'}]})
  const {view,insert}=await stage()
  const exact=(await review()).textContent
  const utf16Length=appendComposerText(props.input,exact).length
  // The existing composer measures JS string length: this emoji occupies two
  // code units. A Unicode-code-point count must not permit a one-unit overrun.
  expect([...appendComposerText(props.input,exact)].length).toBe(utf16Length-1)
  view.rerender(<PixelTextFileInput {...props} limit={utf16Length-1} onInsert={insert}/>)
  expect(screen.getByRole('button',{name:'Insert selected text'})).toBeDisabled()
  view.rerender(<PixelTextFileInput {...props} limit={utf16Length} onInsert={insert}/>)
  fireEvent.click(screen.getByRole('button',{name:'Insert selected text'}))
  expect(insert).toHaveBeenCalledExactlyOnceWith(exact)
})
