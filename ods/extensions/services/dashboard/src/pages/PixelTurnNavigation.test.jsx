import {render} from '../test/test-utils'
import {screen,fireEvent} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation} from '../lib/pixelConversations'

beforeEach(()=>{
  localStorage.clear()
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true,model:'pixel/default'})})))
  globalThis.HTMLElement.prototype.scrollIntoView=vi.fn()
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();delete globalThis.HTMLElement.prototype.scrollIntoView})
it('navigates duplicate prompts by message identity and focuses the chosen turn',async()=>{
  saveConversation({schema:1,chatId:'outline',messages:Array.from({length:12},(_,index)=>({role:index%2?'assistant':'user',content:index%2?'Reply '+index:'Repeated prompt'}))})
  const {container}=render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByLabelText('Jump to conversation turn'),{target:{value:'6'}})
  const target=container.querySelector('[data-pixel-message-index="6"]')
  expect(target).toHaveFocus()
  expect(target.scrollIntoView).toHaveBeenCalledWith({block:'start',behavior:'auto'})
  expect(screen.getByLabelText('Jump to conversation turn')).toHaveValue('')
  fireEvent.click(screen.getByRole('button',{name:'Go to latest message',hidden:true}))
  expect(container.querySelector('[data-pixel-message-index="11"]')).toHaveFocus()
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
})
it('keeps short conversations uncluttered and does not modify a draft while navigating',async()=>{
  saveConversation({schema:1,chatId:'short',messages:[{role:'user',content:'One prompt'}],draft:'Keep draft'})
  render(<Pixel/>);await screen.findByText('Available')
  expect(screen.queryByLabelText('Jump to conversation turn')).toBeNull()
  expect(screen.getByPlaceholderText('Message Assistant...')).toHaveValue('Keep draft')
})
