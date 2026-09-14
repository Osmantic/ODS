import {render, screen, fireEvent, within} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import PixelComposerTools from './PixelComposerTools'
import {readSavedPrompts, SAVED_PROMPTS_KEY} from '../lib/pixelSavedPrompts'

beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
})
afterEach(() => {vi.restoreAllMocks(); delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close})
function mount(input = 'Review this carefully\nKeep the evidence.') {
  const insert = vi.fn()
  render(<MemoryRouter><PixelComposerTools input={input} disabled={false} onInsert={insert}/></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', {name:'Saved prompts'}))
  return insert
}
function create() {
  fireEvent.click(screen.getByRole('button', {name:'Save a new prompt'}))
  fireEvent.change(screen.getByLabelText('Prompt name'), {target:{value:'Review'}})
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
}
it('saves the current draft, edits it, then explicitly inserts reusable text', () => {
  const insert = mount()
  create()
  expect(insert).not.toHaveBeenCalled()
  expect(readSavedPrompts()[0].text).toBe('Review this carefully\nKeep the evidence.')
  fireEvent.click(screen.getByRole('button', {name:'Edit prompt: Review'}))
  fireEvent.change(screen.getByLabelText('Prompt text'), {target:{value:'Revised\nUnicode: Việt'}})
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  fireEvent.click(screen.getByRole('button', {name:'Insert prompt: Review'}))
  expect(insert).toHaveBeenCalledWith('Revised\nUnicode: Việt')
  expect(screen.queryByRole('dialog')).toBeNull()
})
it('confirms deletion and preserves prompts on cancel', () => {
  mount(); create()
  fireEvent.click(screen.getByRole('button', {name:'Delete prompt: Review'}))
  fireEvent.click(screen.getByRole('button', {name:'Keep prompt'}))
  expect(readSavedPrompts()).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', {name:'Delete prompt: Review'}))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', {name:'Delete prompt', exact:true}))
  expect(readSavedPrompts()).toEqual([])
})
it('preserves a conflicting edit from another tab', () => {
  mount(); create()
  fireEvent.click(screen.getByRole('button', {name:'Edit prompt: Review'}))
  const current = readSavedPrompts()
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([{...current[0], text:'Other tab'}]))
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  expect(screen.getByRole('alert')).toHaveTextContent('changed in another tab')
  expect(readSavedPrompts()[0].text).toBe('Other tab')
})
it('does not overwrite malformed storage', () => {
  localStorage.setItem(SAVED_PROMPTS_KEY, '{bad')
  mount(); create()
  expect(screen.getByRole('alert')).toBeVisible()
  expect(localStorage.getItem(SAVED_PROMPTS_KEY)).toBe('{bad')
})
it('bounds library size and refuses an insertion exceeding the current draft budget', () => {
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(Array.from({length:30}, (_, index) => ({id:'p-'+index, title:'Prompt '+index, text:'x'.repeat(1000)}))))
  mount('a'.repeat(16000))
  expect(screen.getByRole('button', {name:'Insert prompt: Prompt 0'})).toBeDisabled()
  create()
  expect(screen.getByRole('alert')).toHaveTextContent('up to 30 prompts')
  expect(readSavedPrompts()).toHaveLength(30)
})
