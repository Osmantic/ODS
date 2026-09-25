import {act, render, screen} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {readConversations, saveConversation} from '../lib/pixelConversations'

const library = 'ods.pixel.conversations.v1'
const healthy = {schema:1,chatId:'healthy-chat',messages:[{role:'user',content:'Keep this conversation'}]}
beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

it.each([
  {messages:[null]},
  {messages:[42]},
  {messages:[{role:'user',content:{damaged:true}}]},
  {messages:[],draft:42},
])('isolates unreadable history without deleting the stored entry (%j)', damaged => {
  const broken = {schema:1,chatId:'damaged-chat',...damaged}
  const raw = JSON.stringify([broken, healthy])
  localStorage.setItem(library, raw)
  render(<PixelConversationNavigation collapsed={false}/>)
  expect(screen.getByRole('button',{name:/^Keep this conversation/})).toBeVisible()
  expect(readConversations().map(chat => chat.chatId)).toEqual(['healthy-chat'])
  expect(localStorage.getItem(library)).toBe(raw)
  // Saving another healthy chat must not silently delete damaged browser data.
  act(() => saveConversation({schema:1,chatId:'new-chat',messages:[{role:'user',content:'New work'}]}))
  expect(JSON.parse(localStorage.getItem(library))).toContainEqual(broken)
})
