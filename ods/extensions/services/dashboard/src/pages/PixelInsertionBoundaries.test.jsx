import {fireEvent, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {SAVED_PROMPTS_KEY} from '../lib/pixelSavedPrompts'

const LIMIT = 16384
beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, json:async () => ({available:true,model:'pixel/default'})})))
})
afterEach(() => {
  vi.unstubAllGlobals(); vi.restoreAllMocks()
  delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close
})
async function composer(value) {
  render(<Pixel/>)
  await screen.findByText('Available')
  const field = screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(field, {target:{value}})
  return field
}
it.each([' ', '\n'])('inserts a saved prompt that exactly fits after existing whitespace %j', async whitespace => {
  const prompt = 'p'.repeat(16000)
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([{id:'boundary',title:'Boundary',text:prompt}]))
  const draft = 'd'.repeat(383) + whitespace
  const field = await composer(draft)
  fireEvent.click(screen.getByRole('button',{name:'Saved prompts'}))
  const insert = screen.getByRole('button',{name:'Insert prompt: Boundary'})
  expect(insert).toBeEnabled()
  fireEvent.click(insert)
  expect(field.value.length).toBe(LIMIT)
  expect(field.value).toBe(draft + prompt)
})
it('still accounts for a newly inserted separator before a saved prompt', async () => {
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([{id:'boundary',title:'Boundary',text:'p'.repeat(16000)}]))
  await composer('d'.repeat(384))
  fireEvent.click(screen.getByRole('button',{name:'Saved prompts'}))
  expect(screen.getByRole('button',{name:'Insert prompt: Boundary'})).toBeDisabled()
})
it('inserts an exactly fitting quoted file without adding a second separator', async () => {
  const text = 'Việt'
  const inserted = '\n\nFile: "note.txt"\n```text\n' + text + '\n```\n'
  const draft = 'd'.repeat(LIMIT - inserted.length - 1) + '\n'
  const field = await composer(draft)
  fireEvent.change(screen.getByLabelText('Choose text file'), {target:{files:[new File([text],'note.txt')]}})
  await screen.findByRole('group',{name:'Review text file'})
  expect(screen.getByRole('button',{name:'Insert file text'})).toBeEnabled()
  fireEvent.click(screen.getByRole('button',{name:'Insert file text'}))
  expect(field.value.length).toBe(LIMIT)
  expect(field.value.endsWith(inserted)).toBe(true)
})
it('counts slash replacement as replacement when a full file fills the composer', async () => {
  const framing = '\n\nFile: "note.txt"\n```text\n\n```\n'
  const text = 'x'.repeat(LIMIT - framing.length)
  const field = await composer('/')
  fireEvent.change(screen.getByLabelText('Choose text file'), {target:{files:[new File([text],'note.txt')]}})
  await screen.findByRole('group',{name:'Review text file'})
  expect(screen.getByRole('button',{name:'Insert file text'})).toBeEnabled()
  fireEvent.click(screen.getByRole('button',{name:'Insert file text'}))
  expect(field.value.length).toBe(LIMIT)
  expect(field.value.startsWith('\n\nFile:')).toBe(true)
})
