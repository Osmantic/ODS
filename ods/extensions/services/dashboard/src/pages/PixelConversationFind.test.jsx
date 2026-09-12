import {render} from '../test/test-utils'
import {screen,fireEvent,within,act} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation,SELECT_EVENT} from '../lib/pixelConversations'

beforeEach(()=>{
  localStorage.clear()
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true,model:'pixel/default'})})))
  globalThis.HTMLElement.prototype.scrollIntoView=vi.fn()
  globalThis.HTMLDialogElement.prototype.showModal=function(){this.open=true}
  globalThis.HTMLDialogElement.prototype.close=function(){this.open=false}
})
afterEach(()=>{
  vi.unstubAllGlobals();vi.restoreAllMocks()
  delete globalThis.HTMLElement.prototype.scrollIntoView
  delete globalThis.HTMLDialogElement.prototype.showModal
  delete globalThis.HTMLDialogElement.prototype.close
})

it('finds a retained reply and focuses its actual message without editing or sending the draft',async()=>{
  saveConversation({schema:1,chatId:'find',messages:[{role:'user',content:'First prompt'},{role:'assistant',content:'Earlier [a.*] evidence'},{role:'user',content:'Latest prompt'},{role:'assistant',content:'Latest answer'}],draft:'Keep my draft'})
  const {container}=render(<Pixel/>);await screen.findByText('Available')
  fireEvent.click(screen.getByLabelText('Chat options'))
  fireEvent.click(screen.getByRole('button',{name:'Find in this conversation'}))
  const dialog=screen.getByRole('dialog',{name:'Find in this conversation'})
  const input=within(dialog).getByLabelText('Search message text')
  fireEvent.change(input,{target:{value:'[A.*]'}})
  expect(within(dialog).getByRole('status')).toHaveTextContent('1 matching message')
  fireEvent.keyDown(input,{key:'Enter',isComposing:true})
  expect(dialog).toHaveAttribute('open')
  fireEvent.click(within(dialog).getByRole('button',{name:'Go to reply message 2'}))
  const target=container.querySelector('[data-pixel-message-index="1"]')
  expect(target).toHaveFocus()
  expect(target.scrollIntoView).toHaveBeenCalledWith({block:'start',behavior:'auto'})
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('Keep my draft')
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
})

it('pages through duplicate matches, reports no results, and restores focus on cancel',async()=>{
  saveConversation({schema:1,chatId:'many',messages:Array.from({length:60},(_,index)=>({role:index%2?'assistant':'user',content:'Repeated evidence '+index}))})
  render(<Pixel/>);await screen.findByText('Available')
  const options=screen.getByLabelText('Chat options')
  fireEvent.click(options)
  fireEvent.click(screen.getByRole('button',{name:'Find in this conversation'}))
  const dialog=screen.getByRole('dialog',{name:'Find in this conversation'})
  fireEvent.change(within(dialog).getByLabelText('Search message text'),{target:{value:'evidence'}})
  expect(within(dialog).getAllByRole('button',{name:/Go to .* message/})).toHaveLength(25)
  fireEvent.click(within(dialog).getByRole('button',{name:'Next results'}))
  expect(within(dialog).getByRole('button',{name:'Go to reply message 26'})).toBeVisible()
  fireEvent.change(within(dialog).getByLabelText('Search message text'),{target:{value:'not present'}})
  expect(within(dialog).getByRole('status')).toHaveTextContent('No matching messages')
  fireEvent.click(within(dialog).getByRole('button',{name:'Close message search'}))
  expect(options).toHaveFocus()
})

it('closes and clears message search when switching conversations',async()=>{
  saveConversation({schema:1,chatId:'other',messages:[{role:'user',content:'Other conversation'}]})
  saveConversation({schema:1,chatId:'current',messages:[{role:'user',content:'Current conversation'}]})
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.click(screen.getByLabelText('Chat options'))
  fireEvent.click(screen.getByRole('button',{name:'Find in this conversation'}))
  fireEvent.change(screen.getByLabelText('Search message text'),{target:{value:'Current'}})
  act(()=>window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'other'})))
  expect(screen.queryByRole('dialog',{name:'Find in this conversation'})).toBeNull()
  const options=screen.getByLabelText('Chat options')
  if (!options.closest('details').open) fireEvent.click(options)
  fireEvent.click(screen.getByRole('button',{name:'Find in this conversation'}))
  expect(screen.getByLabelText('Search message text')).toHaveValue('')
})
