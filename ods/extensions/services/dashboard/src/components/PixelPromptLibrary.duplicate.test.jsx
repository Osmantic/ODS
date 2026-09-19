import {render, screen, fireEvent} from '@testing-library/react'
import PixelPromptLibrary from './PixelPromptLibrary'
import {readSavedPrompts, SAVED_PROMPTS_KEY} from '../lib/pixelSavedPrompts'

const original = {id:'original', title:'Review', text:'Review carefully\nTiếng Việt 🌱'}
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([original]))
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
})
afterEach(() => {vi.restoreAllMocks(); delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close})
function open() {
  const insert = vi.fn()
  render(<PixelPromptLibrary input="Unrelated draft" onInsert={insert}/>)
  fireEvent.click(screen.getByRole('button', {name:'Saved prompts'}))
  return insert
}
function duplicate() {fireEvent.click(screen.getByRole('button', {name:'Duplicate prompt: Review'}))}

it('stages a complete editable variant and saves under a fresh identity only on confirmation', () => {
  const insert = open()
  duplicate()
  expect(screen.getByLabelText('Prompt name')).toHaveValue(original.title)
  expect(screen.getByLabelText('Prompt text')).toHaveValue(original.text)
  expect(readSavedPrompts()).toEqual([original])
  fireEvent.change(screen.getByLabelText('Prompt name'), {target:{value:'Detailed review'}})
  fireEvent.change(screen.getByLabelText('Prompt text'), {target:{value:original.text+'\nCheck edge cases.'}})
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  const [kept, variant] = readSavedPrompts()
  expect(kept).toEqual(original)
  expect(variant).toMatchObject({title:'Detailed review', text:original.text+'\nCheck edge cases.'})
  expect(variant.id).not.toBe(original.id)
  expect(insert).not.toHaveBeenCalled()
  expect(screen.getByRole('button', {name:'Duplicate prompt: Review'})).toHaveFocus()
})

it('cancels without a storage write or insertion', () => {
  const insert = open()
  const write = vi.spyOn(Storage.prototype, 'setItem')
  duplicate()
  fireEvent.click(screen.getByRole('button', {name:'Cancel edit'}))
  expect(write).not.toHaveBeenCalled()
  expect(insert).not.toHaveBeenCalled()
  expect(readSavedPrompts()).toEqual([original])
})

it('keeps a newer source edit from another tab while saving the staged copy', () => {
  open(); duplicate()
  const newer = {...original, text:'Changed elsewhere'}
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([newer]))
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  const prompts = readSavedPrompts()
  expect(prompts[0]).toEqual(newer)
  expect(prompts[1].text).toBe(original.text)
  expect(prompts[1].id).not.toBe(original.id)
})

it('enforces capacity both when opening and after another tab fills the library', () => {
  open(); duplicate()
  const full = [original, ...Array.from({length:29}, (_, i) => ({id:`p-${i}`, title:`Prompt ${i}`, text:'Retain'}))]
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(full))
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  expect(screen.getByRole('alert')).toHaveTextContent('up to 30 prompts')
  expect(readSavedPrompts()).toEqual(full)
  fireEvent.click(screen.getByRole('button', {name:'Cancel edit'}))
  fireEvent(window, new StorageEvent('storage', {key:SAVED_PROMPTS_KEY}))
  expect(screen.getByRole('button', {name:'Duplicate prompt: Review'})).toBeDisabled()
})

it('keeps the variant editable after quota failure and retries without modifying the original', () => {
  open(); duplicate()
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {throw new globalThis.DOMException('Storage full', 'QuotaExceededError')})
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  expect(screen.getByRole('alert')).toHaveTextContent('Storage full')
  expect(screen.getByLabelText('Prompt text')).toHaveValue(original.text)
  expect(readSavedPrompts()).toEqual([original])
  write.mockRestore()
  fireEvent.click(screen.getByRole('button', {name:'Save prompt'}))
  expect(readSavedPrompts()).toHaveLength(2)
  expect(readSavedPrompts()[0]).toEqual(original)
})
