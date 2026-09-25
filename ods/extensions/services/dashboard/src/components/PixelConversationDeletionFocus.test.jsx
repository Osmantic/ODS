import {cleanup, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from '../pages/Pixel'
import PixelConversationNavigation from './PixelConversationNavigation'
import {readConversations, saveConversation} from '../lib/pixelConversations'

const chat = (chatId, text) => ({schema:1,chatId,messages:[{role:'user',content:text}],draft:''})
beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () {this.setAttribute('open','')}
  HTMLDialogElement.prototype.close = function () {this.removeAttribute('open')}
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,json:async () => ({available:true})})))
})
afterEach(() => {cleanup();delete HTMLDialogElement.prototype.showModal;delete HTMLDialogElement.prototype.close;vi.unstubAllGlobals()})

test.each(['last','current','inactive'])('successful deletion of the %s chat moves focus to New task', async selection => {
  if (selection === 'current') saveConversation(chat('keep','Keep this chat'))
  saveConversation(chat('remove','Remove this chat'))
  if (selection === 'inactive') saveConversation(chat('keep','Keep this chat'))
  render(<><PixelConversationNavigation collapsed={false}/><Pixel/></>)
  await screen.findByText('Available')
  const opener = screen.getByRole('button',{name:'Delete chat: Remove this chat'})
  opener.focus()
  fireEvent.click(opener)
  const dialog = screen.getByRole('dialog',{name:'Delete this chat?'})
  fireEvent.click(within(dialog).getByRole('button',{name:'Delete chat',exact:true}))
  expect(readConversations().map(item => item.chatId)).toEqual(selection === 'last' ? [] : ['keep'])
  expect(opener.isConnected).toBe(false)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button',{name:'New task',exact:true})).toHaveFocus()
})

test.each(['Cancel','Escape'])('%s returns focus to the retained delete opener', async action => {
  saveConversation(chat('keep','Keep this chat'))
  render(<><PixelConversationNavigation collapsed={false}/><Pixel/></>)
  await screen.findByText('Available')
  const opener = screen.getByRole('button',{name:'Delete chat: Keep this chat'})
  opener.focus()
  fireEvent.click(opener)
  const dialog = screen.getByRole('dialog',{name:'Delete this chat?'})
  if (action === 'Cancel') fireEvent.click(within(dialog).getByRole('button',{name:'Cancel'}))
  else fireEvent(dialog,new Event('cancel',{cancelable:true}))
  expect(readConversations().map(item => item.chatId)).toEqual(['keep'])
  expect(opener).toHaveFocus()
})
