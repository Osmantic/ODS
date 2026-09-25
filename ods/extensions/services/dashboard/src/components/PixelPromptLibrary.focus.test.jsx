import {render, screen, fireEvent} from '@testing-library/react'
import PixelPromptLibrary from './PixelPromptLibrary'
import {SAVED_PROMPTS_KEY} from '../lib/pixelSavedPrompts'

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([{id:'review', title:'Review', text:'Check the evidence'}]))
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
})
afterEach(() => {
  delete HTMLDialogElement.prototype.showModal
  delete HTMLDialogElement.prototype.close
})
function click(name) {
  const button = screen.getByRole('button', {name, exact:true})
  button.focus()
  fireEvent.click(button)
}
function mount() {
  render(<PixelPromptLibrary input="Draft" disabled={false} onInsert={vi.fn()}/>)
  click('Saved prompts')
}

it.each(['Cancel edit', 'Save prompt'])('returns focus to the edited prompt after %s', action => {
  mount()
  click('Edit prompt: Review')
  click(action)
  expect(screen.getByRole('button', {name:'Edit prompt: Review'})).toHaveFocus()
})

it('keeps keyboard focus in the library after cancelling or confirming deletion', () => {
  mount()
  click('Delete prompt: Review')
  click('Keep prompt')
  expect(screen.getByRole('button', {name:'Delete prompt: Review'})).toHaveFocus()
  click('Delete prompt: Review')
  click('Delete prompt')
  expect(screen.getByRole('button', {name:'Save a new prompt'})).toHaveFocus()
})

it('uses the new-prompt control when an edited prompt leaves the search results', () => {
  mount()
  fireEvent.change(screen.getByRole('searchbox'), {target:{value:'review'}})
  click('Edit prompt: Review')
  fireEvent.change(screen.getByLabelText('Prompt name'), {target:{value:'Inspect'}})
  click('Save prompt')
  expect(screen.getByText('No prompts match your search.')).toBeVisible()
  expect(screen.getByRole('button', {name:'Save a new prompt'})).toHaveFocus()
})

it('returns to the new-prompt control and preserves the external close target', () => {
  mount()
  click('Save a new prompt')
  click('Cancel edit')
  expect(screen.getByRole('button', {name:'Save a new prompt'})).toHaveFocus()
  click('Edit prompt: Review')
  click('Close prompts')
  expect(screen.getByRole('button', {name:'Saved prompts'})).toHaveFocus()
})

it('keeps failed saves in the editor until the user cancels', () => {
  mount()
  click('Edit prompt: Review')
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify([{id:'review', title:'Review', text:'Newer text'}]))
  click('Save prompt')
  expect(screen.getByRole('alert')).toHaveTextContent('changed in another tab')
  expect(screen.getByRole('button', {name:'Save prompt'})).toHaveFocus()
  click('Cancel edit')
  expect(screen.getByRole('button', {name:'Edit prompt: Review'})).toHaveFocus()
})
