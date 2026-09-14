import {render,screen,fireEvent,within} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {ConversationTitle} from './PixelConversationRow'
import {saveConversation,readConversations,SELECT_EVENT} from '../lib/pixelConversations'
import {conversationLabels} from '../lib/pixelConversationLabels'

beforeEach(()=>{
  localStorage.clear()
  saveConversation({schema:1,chatId:'row-test',messages:[{role:'user',content:'My chat'}]})
  HTMLDialogElement.prototype.showModal=function(){this.open=true}
  HTMLDialogElement.prototype.close=function(){this.open=false}
})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();delete HTMLDialogElement.prototype.showModal;delete HTMLDialogElement.prototype.close})

it('opens only supported actions on right click, supports keyboard navigation and restores focus',()=>{
  const selected=vi.fn()
  window.addEventListener(SELECT_EVENT,selected)
  render(<PixelConversationNavigation/> )
  const button=screen.getByRole('button',{name:'My chat',exact:true})
  fireEvent.contextMenu(button,{clientX:900,clientY:700})
  const menu=screen.getByRole('menu')
  expect(within(menu).getAllByRole('menuitem').map(item=>item.textContent)).toEqual(['Rename','Pin','Archive','Duplicate / Fork','Export conversation','Delete chat'])
  expect(screen.getByRole('menuitem',{name:'Rename'})).toHaveFocus()
  fireEvent.keyDown(menu,{key:'ArrowDown'})
  expect(screen.getByRole('menuitem',{name:'Pin'})).toHaveFocus()
  fireEvent.keyDown(menu,{key:'Escape'})
  expect(screen.queryByRole('menu')).toBeNull()
  expect(button).toHaveFocus()
  expect(selected).not.toHaveBeenCalled()
  window.removeEventListener(SELECT_EVENT,selected)
})

it('pins from the context menu without changing messages and keeps deletion confirmation',()=>{
  render(<PixelConversationNavigation/> )
  fireEvent.keyDown(screen.getByRole('button',{name:'My chat',exact:true}),{key:'F10',shiftKey:true})
  fireEvent.click(screen.getByRole('menuitem',{name:'Pin'}))
  expect(conversationLabels('row-test').pinned).toBe(true)
  expect(readConversations()[0].messages[0].content).toBe('My chat')
  fireEvent.contextMenu(screen.getByRole('button',{name:'My chat',exact:true}))
  expect(screen.getByRole('menuitem',{name:'Unpin'})).toBeVisible()
  fireEvent.click(screen.getByRole('menuitem',{name:'Delete chat'}))
  expect(screen.getByRole('dialog',{name:'Delete this chat?'})).toBeVisible()
  expect(readConversations()).toHaveLength(1)
})

it('measures only overflowing titles for the hover animation',()=>{
  vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(120)
  vi.spyOn(HTMLElement.prototype,'scrollWidth','get').mockImplementation(function(){return this.textContent==='Long title'?300:60})
  const view=render(<ConversationTitle title="Long title"/>)
  expect(view.container.firstChild).toHaveAttribute('data-overflow','true')
  expect(view.container.firstChild.style.getPropertyValue('--title-travel')).toBe('-180px')
  view.rerender(<ConversationTitle title="Short"/>)
  expect(view.container.firstChild).toHaveAttribute('data-overflow','false')
})
