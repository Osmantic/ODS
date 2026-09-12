import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPromptLibrary from './PixelPromptLibrary'
import {SAVED_PROMPTS_KEY} from '../lib/pixelSavedPrompts'

const existing = {id:'prompt-existing',title:'Review',text:'Original text'}
const backup = prompts => JSON.stringify({schemaVersion:1,kind:'ods-pixel-prompts',prompts})
beforeEach(()=>{
  localStorage.setItem(SAVED_PROMPTS_KEY,JSON.stringify([existing]))
  HTMLDialogElement.prototype.showModal=function(){this.open=true}
  HTMLDialogElement.prototype.close=function(){this.open=false}
})
afterEach(()=>{vi.restoreAllMocks();delete HTMLDialogElement.prototype.showModal;delete HTMLDialogElement.prototype.close})
function open() {
  const insert=vi.fn()
  render(<PixelPromptLibrary input="Retained draft" disabled={false} onInsert={insert}/>)
  fireEvent.click(screen.getByRole('button',{name:'Saved prompts'}))
  return insert
}
async function select(text) {
  fireEvent.change(screen.getByLabelText('Import prompt backup'),{target:{files:[new File([text],'backup.json',{type:'application/json'})]}})
  await screen.findByRole('button',{name:'Confirm prompt import'})
}
it('stages then merges exact text while preserving concurrent edits and skipping duplicates',async()=>{
  const insert=open()
  await select(backup([existing,{title:'Review',text:'Việt\r\n```js\n1\n```'}]))
  expect(JSON.parse(localStorage.getItem(SAVED_PROMPTS_KEY))).toEqual([existing])
  const concurrent={id:'prompt-other',title:'Other',text:'Concurrent edit'}
  localStorage.setItem(SAVED_PROMPTS_KEY,JSON.stringify([existing,concurrent]))
  fireEvent.click(screen.getByRole('button',{name:'Confirm prompt import'}))
  const saved=JSON.parse(localStorage.getItem(SAVED_PROMPTS_KEY))
  expect(saved).toHaveLength(3)
  expect(saved.slice(0,2)).toEqual([existing,concurrent])
  expect(saved[2]).toMatchObject({title:'Review',text:'Việt\r\n```js\n1\n```'})
  expect(saved[2].id).toMatch(/^prompt-[a-f0-9-]+$/)
  expect(insert).not.toHaveBeenCalled()
})
it('rejects overflow atomically and clears staged imports when the dialog closes',async()=>{
  open()
  await select(backup(Array.from({length:30},(_,i)=>({title:`Prompt ${i}`,text:'Text'}))))
  fireEvent.click(screen.getByRole('button',{name:'Confirm prompt import'}))
  expect(screen.getByRole('alert')).toHaveTextContent('exceed 30')
  expect(JSON.parse(localStorage.getItem(SAVED_PROMPTS_KEY))).toEqual([existing])
  fireEvent.click(screen.getByRole('button',{name:'Close prompts'}))
  fireEvent.click(screen.getByRole('button',{name:'Saved prompts'}))
  expect(screen.queryByRole('button',{name:'Confirm prompt import'})).toBeNull()
})
it('exports only versioned prompt text and names from current storage',async()=>{
  let blob
  vi.spyOn(URL,'createObjectURL').mockImplementation(value=>{blob=value;return 'blob:backup'})
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
  open()
  fireEvent.click(screen.getByRole('button',{name:'Download prompt backup'}))
  const text=await new Promise(resolve=>{const reader=new globalThis.FileReader();reader.onload=()=>resolve(reader.result);reader.readAsText(blob)})
  expect(JSON.parse(text)).toEqual({schemaVersion:1,kind:'ods-pixel-prompts',prompts:[{title:'Review',text:'Original text'}]})
})
it('preserves unreadable local storage and rejects malformed imported records',async()=>{
  open()
  fireEvent.change(screen.getByLabelText('Import prompt backup'),{target:{files:[new File([backup([{title:'Bad',text:''}])],'bad.json')]}})
  await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('Each prompt needs'))
  await select(backup([{title:'Good',text:'Useful'}]))
  localStorage.setItem(SAVED_PROMPTS_KEY,'broken')
  fireEvent.click(screen.getByRole('button',{name:'Confirm prompt import'}))
  expect(screen.getByRole('alert')).toBeInTheDocument()
  expect(localStorage.getItem(SAVED_PROMPTS_KEY)).toBe('broken')
})
