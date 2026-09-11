import {fireEvent,screen,waitFor,act} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {SEND_KEY_STORAGE,shouldSendMessage} from '../lib/usePixelSendKey'

beforeEach(()=>{
  localStorage.clear()
  vi.stubGlobal('fetch',vi.fn(async url=>url==='/api/pixel/chat/stream' ? {ok:false,status:503,json:async()=>({detail:'fixture'})} : {ok:true,json:async()=>({available:true,model:'pixel/default'})}))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
it('keeps Enter for newlines in modifier mode and sends once with Ctrl+Enter',async()=>{
  render(<Pixel/>)
  await screen.findByText('Available')
  fireEvent.change(screen.getByLabelText('Send shortcut'),{target:{value:'mod-enter'}})
  const field=screen.getByPlaceholderText('Message Assistant...')
  fireEvent.change(field,{target:{value:'Multiline\nprompt'}})
  fireEvent.keyDown(field,{key:'Enter'})
  expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')).toHaveLength(0)
  expect(field).toHaveValue('Multiline\nprompt')
  fireEvent.keyDown(field,{key:'Enter',ctrlKey:true})
  await waitFor(()=>expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')).toHaveLength(1))
  expect(localStorage.getItem(SEND_KEY_STORAGE)).toBe('mod-enter')
})
it('restores the preference and refreshes it from another tab without changing the draft',async()=>{
  localStorage.setItem(SEND_KEY_STORAGE,'mod-enter')
  render(<Pixel/>);await screen.findByText('Available')
  expect(screen.getByLabelText('Send shortcut')).toHaveValue('mod-enter')
  fireEvent.change(screen.getByPlaceholderText('Message Assistant...'),{target:{value:'Keep this draft'}})
  localStorage.setItem(SEND_KEY_STORAGE,'enter')
  act(()=>window.dispatchEvent(new globalThis.StorageEvent('storage',{key:SEND_KEY_STORAGE})))
  expect(screen.getByLabelText('Send shortcut')).toHaveValue('enter')
  expect(screen.getByPlaceholderText('Message Assistant...')).toHaveValue('Keep this draft')
})
it('keeps a session preference usable when browser storage rejects the write',async()=>{
  vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('blocked')},removeItem:()=>{}})
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByLabelText('Send shortcut'),{target:{value:'mod-enter'}})
  expect(screen.getByLabelText('Send shortcut')).toHaveValue('mod-enter')
  expect(screen.getByText('This shortcut applies to this tab, but could not be saved for next time.')).toBeInTheDocument()
})
it.each([
  [{key:'Enter',metaKey:true},'mod-enter',true],
  [{key:'Enter',ctrlKey:true,shiftKey:true},'mod-enter',false],
  [{key:'Enter',altKey:true},'enter',false],
  [{key:'Enter',ctrlKey:true,nativeEvent:{isComposing:true}},'mod-enter',false],
  [{key:'Enter',keyCode:229},'enter',false],
  [{key:'Enter'},'enter',true],
])('preserves composition/newline modifiers at the send boundary', (event,mode,expected)=>expect(shouldSendMessage(event,mode)).toBe(expected))
