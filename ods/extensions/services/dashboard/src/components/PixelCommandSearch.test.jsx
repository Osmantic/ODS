import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PixelCommandSearch, { OPEN_PIXEL_SEARCH } from './PixelCommandSearch'
import { saveConversation, deleteConversation, SELECT_EVENT } from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
})

test('keeps keyboard-selected results visible and announces the selected action', () => {
  const scroll = vi.fn()
  const previous = globalThis.Element.prototype.scrollIntoView
  globalThis.Element.prototype.scrollIntoView = scroll
  try {
    render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
    fireEvent(window, new Event(OPEN_PIXEL_SEARCH))
    const input = screen.getByLabelText('Search conversations and actions')
    fireEvent.keyDown(input, {key:'ArrowUp'})
    const active = screen.getByRole('button', {name:/Research with evidence/})
    expect(scroll).toHaveBeenLastCalledWith({block:'nearest'})
    expect(scroll.mock.instances.at(-1)).toBe(active)
    expect(screen.getByRole('status')).toHaveTextContent('4 of 4: Research with evidence')
    expect(input).toHaveFocus()
    fireEvent.change(input, {target:{value:'does not exist'}})
    expect(screen.getByRole('status')).toHaveTextContent('No results')
  } finally {
    if (previous) globalThis.Element.prototype.scrollIntoView = previous
    else delete globalThis.Element.prototype.scrollIntoView
  }
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

test('removes deleted conversations from an open search and never selects a stale result', () => {
  saveConversation({schema:1,chatId:'removed',messages:[{role:'user',content:'Find this task'}]})
  const selected = vi.fn()
  window.addEventListener(SELECT_EVENT, selected)
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
  fireEvent(window, new Event(OPEN_PIXEL_SEARCH))
  const input = screen.getByLabelText('Search conversations and actions')
  fireEvent.change(input,{target:{value:'Find this task'}})
  act(() => deleteConversation('removed'))
  expect(screen.queryByRole('button',{name:/Find this task/})).toBeNull()
  fireEvent.keyDown(input,{key:'Enter'})
  expect(selected).not.toHaveBeenCalled()
  expect(input).toHaveValue('Find this task')
  window.removeEventListener(SELECT_EVENT, selected)
})

test('refreshes new saved results without clearing the open search query', () => {
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
  fireEvent(window, new Event(OPEN_PIXEL_SEARCH))
  const input = screen.getByLabelText('Search conversations and actions')
  fireEvent.change(input,{target:{value:'Newly saved'}})
  localStorage.setItem('ods.pixel.conversations.v1',JSON.stringify([{schema:1,chatId:'newly-saved',messages:[{role:'user',content:'Newly saved task'}]}]))
  fireEvent(window,new StorageEvent('storage',{key:'ods.pixel.conversations.v1'}))
  expect(screen.getByRole('button',{name:/Newly saved task/})).toBeVisible()
  expect(input).toHaveValue('Newly saved')
})

test.each(['ctrlKey','metaKey'])('repeated %s search shortcuts preserve the query and original focus return target', modifier => {
  render(<MemoryRouter><button>Original trigger</button><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
  const trigger = screen.getByRole('button',{name:'Original trigger'})
  trigger.focus()
  fireEvent.keyDown(trigger,{key:'k',[modifier]:true})
  const field = screen.getByLabelText('Search conversations and actions')
  fireEvent.change(field,{target:{value:'Research'}})
  fireEvent.keyDown(field,{key:'k',[modifier]:true})
  expect(field).toHaveValue('Research')
  fireEvent.click(screen.getByRole('button',{name:'Close search'}))
  expect(trigger).toHaveFocus()
})
