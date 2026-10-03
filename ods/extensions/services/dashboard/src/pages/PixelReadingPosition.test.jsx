import {render} from '../test/test-utils'
import {screen, fireEvent, waitFor} from '@testing-library/react'
import Pixel from './Pixel'
import {saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  saveConversation({schema:1,chatId:'reading',messages:[{role:'user',content:'Earlier prompt'},{role:'assistant',content:'Earlier response'}]})
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/pixel/chat/stream') {
      const frames = ['data: {"choices":[{"delta":{"content":"New reply"}}]}\n\n', 'data: [DONE]\n\n']
      return {ok:true, headers:new Map([['content-type','text/event-stream']]), body:{getReader:() => ({read:async() => frames.length ? {done:false,value:new TextEncoder().encode(frames.shift())} : {done:true},releaseLock(){}})}}
    }
    return {ok:true,json:async() => ({available:true,model:'pixel/default'})}
  }))
})
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})

async function setup(top) {
  render(<Pixel/>); await screen.findByText('Available')
  const region = screen.getByRole('region',{name:'Conversation messages'})
  Object.defineProperties(region,{scrollHeight:{configurable:true,value:2000},clientHeight:{configurable:true,value:500}})
  region.scrollTop = top
  fireEvent.scroll(region)
  return region
}
async function send() {
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Continue'}})
  fireEvent.keyDown(screen.getByPlaceholderText('Message Portal...'),{key:'Enter'})
  await screen.findByText('New reply')
}
it('keeps an older reading position through stream updates and resumes on explicit jump', async () => {
  const region = await setup(300)
  await send()
  expect(region.scrollTop).toBe(300)
  fireEvent.click(screen.getByRole('button',{name:'Jump to latest'}))
  expect(region.scrollTop).toBe(2000)
  expect(region).toHaveFocus()
  expect(screen.queryByRole('button',{name:'Jump to latest'})).toBeNull()
})
it('continues following when the owner is near the bottom', async () => {
  const region = await setup(1480)
  await send()
  expect(region.scrollTop).toBe(2000)
  expect(screen.queryByRole('button',{name:'Jump to latest'})).toBeNull()
})
it('resets following when selecting another retained conversation', async () => {
  const region = await setup(300)
  saveConversation({schema:1,chatId:'other',messages:[{role:'user',content:'Other history'}]})
  fireEvent(window,new CustomEvent(SELECT_EVENT,{detail:'other'}))
  await screen.findByText('Other history')
  await waitFor(() => expect(region.scrollTop).toBe(2000))
  expect(screen.queryByRole('button',{name:'Jump to latest'})).toBeNull()
})
