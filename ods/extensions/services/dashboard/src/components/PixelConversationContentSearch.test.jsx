import {render, screen, fireEvent} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import PixelCommandSearch from './PixelCommandSearch'
import {saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
})
afterEach(() => {delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close})
const chat = (id, messages, draft = '') => saveConversation({schema:1, chatId:id, messages, draft})
function search(query) {
  render(<MemoryRouter><PixelCommandSearch onInsert={() => {}} onNewTask={() => {}}/></MemoryRouter>)
  fireEvent.keyDown(window, {key:'k', ctrlKey:true})
  fireEvent.change(screen.getByLabelText('Search conversations and actions'), {target:{value:query}})
}

it('finds a later reply and selects its exact conversation despite identical titles', () => {
  chat('first', [{role:'user', content:'Analyze this'}, {role:'assistant', content:'Budget: 42 euros'}])
  chat('second', [{role:'user', content:'Analyze this'}, {role:'assistant', content:'Different result'}])
  const selected = vi.fn()
  window.addEventListener(SELECT_EVENT, selected)
  try {
    search('42 EUROS')
    expect(screen.getByText('Reply: Budget: 42 euros')).toBeVisible()
    fireEvent.keyDown(screen.getByLabelText('Search conversations and actions'), {key:'Enter'})
    expect(selected).toHaveBeenCalledOnce()
    expect(selected.mock.calls[0][0].detail).toBe('first')
  } finally {window.removeEventListener(SELECT_EVENT, selected)}
})

it('searches beyond the 80-character title and shows a bounded matching excerpt', () => {
  chat('long', [{role:'user', content:'a'.repeat(500) + ' exact phrase ' + 'b'.repeat(500)}])
  search('exact phrase')
  const snippet = screen.getByText(/Message: ….*exact phrase/)
  expect(snippet.textContent.length).toBeLessThan(200)
  expect(snippet.textContent.endsWith('…')).toBe(true)
})

it('searches draft text without issuing a model request or changing it', () => {
  chat('draft', [{role:'user', content:'Earlier task'}], 'Hẹn gặp lại tomorrow')
  const before = localStorage.getItem('ods.pixel.chat.v1')
  search('gặp lại')
  expect(screen.getByText('Draft: Hẹn gặp lại tomorrow')).toBeVisible()
  expect(localStorage.getItem('ods.pixel.chat.v1')).toBe(before)
})

it('treats literal regular-expression syntax and HTML as inert text', () => {
  chat('literal', [{role:'user', content:'Read result'}, {role:'assistant', content:'<img src=x onerror=bad()> [a.*]'}])
  search('[a.*]')
  expect(screen.getByText(/Reply: <img/)).toBeVisible()
  expect(document.querySelector('img')).toBeNull()
})

it('does not match a metadata-only value or create synthetic conversation content', () => {
  saveConversation({schema:1, chatId:'meta', messages:[{role:'user', content:'Task', task:{detail:'hiddenmagic'}}]})
  search('hiddenmagic')
  expect(screen.getByText('No matching conversations or actions.')).toBeVisible()
})

it('keeps a complete long matching phrase after preceding context', () => {
  const phrase = 'matching phrase '.repeat(15).trim()
  chat('long-phrase', [{role:'user', content:'Find this reply'}, {role:'assistant', content:'x'.repeat(300) + phrase + 'z'.repeat(300)}])
  search(phrase)
  const snippet = screen.getByText(/^Reply: …/)
  expect(snippet.textContent).toContain(phrase)
  expect(snippet.textContent.length).toBeLessThan(phrase.length + 70)
})

it('maps case-expanded Unicode prefixes back to retained text offsets', () => {
  chat('unicode-offset', [{role:'user', content:'Find this reply'}, {role:'assistant', content:'İ'.repeat(300) + ' exact phrase ' + 'z'.repeat(300)}])
  search('EXACT PHRASE')
  expect(screen.getByText(/^Reply: …/).textContent).toContain('exact phrase')
})
