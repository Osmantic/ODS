import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PixelCommandSearch, { OPEN_PIXEL_SEARCH } from './PixelCommandSearch'
import { saveConversation, SELECT_EVENT } from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
})
test('Ctrl K searches real saved conversations and selects the exact identity', () => {
  saveConversation({schema:1,chatId:'saved-one',messages:[{role:'user',content:'Build a clock'}]})
  const selected = vi.fn()
  window.addEventListener(SELECT_EVENT, selected)
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
  fireEvent.keyDown(window, {key:'k',ctrlKey:true})
  fireEvent.change(screen.getByLabelText('Search conversations and actions'), {target:{value:'clock'}})
  fireEvent.keyDown(screen.getByLabelText('Search conversations and actions'), {key:'Enter'})
  expect(selected.mock.calls[0][0].detail).toBe('saved-one')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  window.removeEventListener(SELECT_EVENT, selected)
})
test('handles empty results without issuing an action', () => {
  const create = vi.fn()
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={create}/></MemoryRouter>)
  fireEvent(window, new Event(OPEN_PIXEL_SEARCH))
  fireEvent.change(screen.getByLabelText('Search conversations and actions'), {target:{value:'no-such-conversation'}})
  fireEvent.keyDown(screen.getByLabelText('Search conversations and actions'), {key:'ArrowDown'})
  fireEvent.keyDown(screen.getByLabelText('Search conversations and actions'), {key:'Enter'})
  expect(create).not.toHaveBeenCalled()
  expect(screen.getByText('No matching conversations or actions.')).toBeVisible()
})
