import {render,screen,fireEvent,within} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {saveConversation,readConversations,DELETE_EVENT,SELECT_EVENT,deleteConversation} from '../lib/pixelConversations'

beforeEach(()=>{
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','')}
  HTMLDialogElement.prototype.close = function(){this.removeAttribute('open')}
  saveConversation({schema:1,chatId:'delete-test',messages:[{role:'user',content:'Disposable test'}]})
})
afterEach(()=>{delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; vi.restoreAllMocks()})
test('delete control does not select the chat; cancellation preserves it',()=>{
  const select = vi.fn(); window.addEventListener(SELECT_EVENT,select)
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.click(screen.getByRole('button',{name:'Delete chat: Disposable test'}))
  const dialog=screen.getByRole('dialog',{name:'Delete this chat?'})
  expect(within(dialog).getByText(/Workspace files and published previews are kept/)).toBeVisible()
  expect(select).not.toHaveBeenCalled()
  fireEvent.click(within(dialog).getByRole('button',{name:'Cancel'}))
  expect(readConversations()).toHaveLength(1)
  window.removeEventListener(SELECT_EVENT,select)
})
test('deletes after confirmation and shows refusal errors without hiding the chat',()=>{
  let blocked=true
  const handle=event=>{if(blocked) event.detail.complete('Stop the current task first.'); else {deleteConversation(event.detail.chatId);event.detail.complete('')}}
  window.addEventListener(DELETE_EVENT,handle)
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.click(screen.getByRole('button',{name:'Delete chat: Disposable test'}))
  fireEvent.click(screen.getByRole('button',{name:'Delete chat',exact:true}))
  expect(screen.getByRole('alert')).toHaveTextContent('Stop the current task first.')
  expect(readConversations()).toHaveLength(1)
  blocked=false
  fireEvent.click(screen.getByRole('button',{name:'Delete chat',exact:true}))
  expect(readConversations()).toEqual([])
  expect(screen.queryByRole('button',{name:'Disposable test',exact:true})).not.toBeInTheDocument()
  window.removeEventListener(DELETE_EVENT,handle)
})
