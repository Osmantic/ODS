import {render, screen, fireEvent} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import PixelCommandSearch, {OPEN_PIXEL_SEARCH} from './PixelCommandSearch'

beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
})
afterEach(() => {
  delete HTMLDialogElement.prototype.showModal
  delete HTMLDialogElement.prototype.close
})

test('leaves IME candidate keys to text composition before activating a result', () => {
  const create = vi.fn()
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={create}/></MemoryRouter>)
  fireEvent(window, new Event(OPEN_PIXEL_SEARCH))
  const input = screen.getByLabelText('Search conversations and actions')
  fireEvent.compositionStart(input)
  expect(fireEvent.keyDown(input, {key:'ArrowDown',isComposing:true})).toBe(true)
  expect(fireEvent.keyDown(input, {key:'Enter',isComposing:true})).toBe(true)
  expect(create).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog')).toBeVisible()
  fireEvent.compositionEnd(input)
  fireEvent.keyDown(input, {key:'Enter'})
  expect(create).toHaveBeenCalledOnce()
  expect(screen.queryByRole('dialog')).toBeNull()
})

test('does not open global search for a shortcut belonging to composition', () => {
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
  fireEvent.keyDown(window, {key:'k',ctrlKey:true,isComposing:true})
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.keyDown(window, {key:'k',ctrlKey:true})
  expect(screen.getByRole('dialog')).toBeVisible()
})
