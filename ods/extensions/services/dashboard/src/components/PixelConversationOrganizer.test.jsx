import {act, fireEvent, render, screen, within} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {CHAT_KEY, saveConversation, readConversations, conversationTitle} from '../lib/pixelConversations'
import {saveConversationLabels, conversationLabels} from '../lib/pixelConversationLabels'

const chat = {schema:1, chatId:'one', messages:[{role:'user', content:'Original prompt'}], inFlight:true, draft:'Keep draft'}
beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false}
  saveConversation(chat)
})
afterEach(() => {vi.restoreAllMocks(); delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close})
const open = () => fireEvent.click(screen.getByRole('button', {name:/Organize chat:/}))
const save = () => fireEvent.click(screen.getByRole('button', {name:'Save labels'}))

it('renames and pins a chat without modifying active work, draft or message history', () => {
  const before = localStorage.getItem(CHAT_KEY)
  const {unmount} = render(<PixelConversationNavigation/>)
  open()
  fireEvent.change(screen.getByLabelText('Conversation name'), {target:{value:'  Budget research  '}})
  fireEvent.click(screen.getByLabelText('Pin conversation'))
  save()
  expect(screen.getByRole('button', {name:/^Budget research/})).toBeVisible()
  expect(screen.getByText('Pinned')).toBeVisible()
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
  act(() => saveConversation({...chat, messages:[...chat.messages, {role:'assistant', content:'New reply'}]}))
  expect(conversationTitle(readConversations()[0])).toBe('Budget research')
  unmount()
  render(<PixelConversationNavigation/>)
  expect(screen.getByText('Pinned')).toBeVisible()
  expect(readConversations()[0].inFlight).toBe(true)
})

it('archives and restores the same conversation without deleting or stopping it', () => {
  render(<PixelConversationNavigation/>)
  open(); fireEvent.click(screen.getByLabelText('Archive conversation')); save()
  expect(screen.queryByRole('button', {name:/^Original prompt/})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Archived (1)'}))
  expect(screen.getByRole('button', {name:/^Original prompt/})).toBeVisible()
  open(); fireEvent.click(screen.getByLabelText('Archive conversation')); save()
  fireEvent.click(screen.getByRole('button', {name:'Show active conversations'}))
  expect(screen.getByRole('button', {name:/^Original prompt/})).toBeVisible()
  expect(readConversations()[0].messages).toEqual(chat.messages)
})

it('does not overwrite a newer label change from another tab', () => {
  render(<PixelConversationNavigation/>)
  open()
  fireEvent.change(screen.getByLabelText('Conversation name'), {target:{value:'My edit'}})
  act(() => saveConversationLabels('one', {title:'Other tab', pinned:false, archived:false}))
  save()
  expect(screen.getByRole('alert')).toHaveTextContent('Labels changed in another tab')
  expect(conversationLabels('one').title).toBe('Other tab')
})

it('shows failed writes and preserves unreadable metadata', () => {
  localStorage.setItem('ods.pixel.chat-labels.v1.one', '{broken')
  render(<PixelConversationNavigation/>)
  open(); save()
  expect(screen.getByRole('alert')).toBeVisible()
  expect(localStorage.getItem('ods.pixel.chat-labels.v1.one')).toBe('{broken')
  expect(readConversations()).toHaveLength(1)
})

it('resets to the automatic name and synchronizes storage events', () => {
  saveConversationLabels('one', {title:'Custom', pinned:false, archived:false})
  render(<PixelConversationNavigation/>)
  open()
  fireEvent.change(screen.getByLabelText('Conversation name'), {target:{value:''}})
  save()
  expect(screen.getByRole('button', {name:/^Original prompt/})).toBeVisible()
  localStorage.setItem('ods.pixel.chat-labels.v1.one', JSON.stringify({title:'Remote edit', pinned:false, archived:false}))
  act(() => window.dispatchEvent(new Event('storage')))
  expect(screen.getByRole('button', {name:/^Remote edit/})).toBeVisible()
})

it('cancels without persisting labels or selecting another conversation', () => {
  render(<PixelConversationNavigation/>)
  open()
  fireEvent.change(screen.getByLabelText('Conversation name'), {target:{value:'Discard this'}})
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', {name:'Cancel'}))
  expect(conversationLabels('one').title).toBe('')
  expect(screen.getByRole('button', {name:'Organize chat: Original prompt'})).toHaveFocus()
})
