import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import PixelComposerTools from './PixelComposerTools'
import {readSavedPrompts, SAVED_PROMPTS_KEY} from '../lib/pixelSavedPrompts'

const prompt = (id, title = 'Review', text = 'Read carefully') => ({id, title, text})
const archive = prompts => JSON.stringify({schemaVersion:1, kind:'ods-saved-prompts', prompts})
const seed = items => localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(items))
function mount() {
  const insert = vi.fn()
  render(<MemoryRouter><PixelComposerTools input="Keep this draft" disabled={false} onInsert={insert}/></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', {name:'Saved prompts'}))
  return insert
}
function upload(raw, overrides = {}) {
  const file = new File([raw], 'prompts.json', {type:'application/json'})
  Object.assign(file, {arrayBuffer:async () => new TextEncoder().encode(raw).buffer}, overrides)
  fireEvent.change(screen.getByLabelText('Choose prompt backup'), {target:{files:[file]}})
  return file
}
const readBlob = blob => new Promise((resolve, reject) => {
  const reader = new globalThis.FileReader()
  reader.onload = () => resolve(reader.result)
  reader.onerror = reject
  reader.readAsText(blob)
})
beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
})
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals()
  delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close
})

it('exports a fresh library and restores exact Unicode text only after review', async () => {
  const original = [prompt('one', 'Việt 🧪', '```js\nconst x = "☀";\n```\n\tEnd'), prompt('two')]
  seed(original)
  const insert = mount()
  let blob
  const create = vi.fn(value => {blob = value; return 'blob:prompt-backup'})
  vi.stubGlobal('URL', class extends URL {static createObjectURL = create; static revokeObjectURL = vi.fn()})
  let filename
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {filename = this.download})
  // The export reads saved state at click time, rather than the rendered list.
  original.push(prompt('three', 'Latest', 'Changed in another tab'))
  seed(original)
  fireEvent.click(screen.getByRole('button', {name:'Export prompts'}))
  expect(filename).toBe('ods-saved-prompts.json')
  const raw = await readBlob(blob)
  expect(JSON.parse(raw)).toEqual({schemaVersion:1, kind:'ods-saved-prompts', prompts:original})
  seed([])
  upload(raw)
  await screen.findByRole('button', {name:'Import reviewed prompts'})
  expect(readSavedPrompts()).toEqual([])
  expect(insert).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  expect(readSavedPrompts()).toEqual(original)
  expect(insert).not.toHaveBeenCalled()
  expect(screen.getByText('Imported 3 prompts; skipped 0 matching prompts.')).toBeVisible()
})

it('preserves later edits and conflicting IDs, and makes repeated imports idempotent', async () => {
  seed([prompt('one', 'Old', 'Old text')]); mount()
  const raw = archive([prompt('one', 'Backup', 'Backup text'), prompt('two')])
  upload(raw)
  await screen.findByRole('button', {name:'Import reviewed prompts'})
  const current = prompt('one', 'Newer edit', 'Keep this')
  seed([current])
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  const restored = readSavedPrompts()
  expect(restored[0]).toEqual(current)
  expect(restored).toHaveLength(3)
  expect(restored[1]).toMatchObject({title:'Backup', text:'Backup text'})
  expect(new Set(restored.map(item => item.id)).size).toBe(3)
  upload(raw)
  await screen.findByRole('button', {name:'Import reviewed prompts'})
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  expect(readSavedPrompts()).toEqual(restored)
  expect(screen.getByText('Imported 0 prompts; skipped 2 matching prompts.')).toBeVisible()
})

it.each(['{broken', archive([prompt('same'), prompt('same')]),
  JSON.stringify({schemaVersion:2, kind:'ods-saved-prompts', prompts:[]}),
  archive([prompt('one', 'Too long', 'x'.repeat(16001))])])('preserves the library when rejecting an invalid backup', async raw => {
  const existing = [prompt('existing')]
  seed(existing); mount(); upload(raw)
  expect(await screen.findByRole('alert')).toHaveTextContent('not a valid')
  expect(readSavedPrompts()).toEqual(existing)
  expect(screen.queryByRole('button', {name:'Import reviewed prompts'})).toBeNull()
})

it('rejects an oversized file before reading and rejects invalid UTF-8', async () => {
  mount()
  const read = vi.fn()
  upload('x'.repeat(3 * 1024 * 1024 + 1), {arrayBuffer:read})
  expect(read).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('no larger than 3 MB')
  upload('bad', {arrayBuffer:async () => new Uint8Array([255]).buffer})
  expect(await screen.findByRole('alert')).toHaveTextContent('not a valid')
  expect(readSavedPrompts()).toEqual([])
})

it('keeps the previous library on capacity and storage failures', async () => {
  const existing = Array.from({length:30}, (_, index) => prompt('p-' + index, 'Prompt ' + index))
  seed(existing); mount(); upload(archive([prompt('new')]))
  await screen.findByRole('button', {name:'Import reviewed prompts'})
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  expect(screen.getByRole('alert')).toHaveTextContent('exceed 30')
  expect(readSavedPrompts()).toEqual(existing)
  seed([existing[0]])
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {throw new globalThis.DOMException('Storage full', 'QuotaExceededError')})
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  expect(readSavedPrompts()).toEqual([existing[0]])
  expect(screen.getByRole('button', {name:'Import reviewed prompts'})).toBeEnabled()
  write.mockRestore()
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  expect(readSavedPrompts()).toEqual([existing[0], prompt('new')])
})

it('does not overwrite unreadable browser data', async () => {
  mount(); upload(archive([prompt('new')]))
  await screen.findByRole('button', {name:'Import reviewed prompts'})
  localStorage.setItem(SAVED_PROMPTS_KEY, '{broken')
  fireEvent.click(screen.getByRole('button', {name:'Import reviewed prompts'}))
  expect(localStorage.getItem(SAVED_PROMPTS_KEY)).toBe('{broken')
  expect(screen.getByRole('alert')).toHaveTextContent('preserved')
})

it('discards pending file reads when the dialog closes', async () => {
  mount()
  let finish
  upload(archive([prompt('late')]), {arrayBuffer:() => new Promise(resolve => {finish = resolve})})
  fireEvent.click(screen.getByRole('button', {name:'Close prompts'}))
  await act(async () => {finish(new TextEncoder().encode(archive([prompt('late')])).buffer)})
  fireEvent.click(screen.getByRole('button', {name:'Saved prompts'}))
  await waitFor(() => expect(screen.queryByRole('button', {name:'Import reviewed prompts'})).toBeNull())
  expect(readSavedPrompts()).toEqual([])
})
