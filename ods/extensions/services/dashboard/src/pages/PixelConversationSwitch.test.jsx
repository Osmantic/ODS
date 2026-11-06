// Opening a conversation from the sidebar is not an edit: the list must not
// reorder under the pointer, or the highlight seems to stay where it was.
vi.mock('../lib/useVerifiedPreview', () => ({default: (_preview, access) => access}))
import {render} from '../test/test-utils'
import {act, fireEvent, screen} from '@testing-library/react'
import Pixel from './Pixel'
import PixelConversationNavigation from '../components/PixelConversationNavigation'
import {readConversations, saveConversation} from '../lib/pixelConversations'

const host = vi.fn(async () => ({ok: true, status: 200, json: async () => ({available: true, model: 'pixel/default'})}))
const chat = (chatId, content) => ({schema: 1, chatId, draft: '', messages: [
  {role: 'user', content},
  {role: 'assistant', content: 'Done.', status: 'done'},
]})
const rows = () => [...document.querySelectorAll('.conversation-link')]
const titles = () => rows().map(row => row.title)
const highlighted = () => rows().filter(row => row.getAttribute('aria-current') === 'page').map(row => row.title)

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', host)
  const now = vi.spyOn(Date, 'now')
  now.mockReturnValue(1000); saveConversation(chat('older', 'Older task'))
  now.mockReturnValue(2000); saveConversation(chat('newer', 'Newer task'))
  now.mockRestore()
})
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear() })

it('opening an older conversation highlights it where it is and keeps the order', async () => {
  render(<><PixelConversationNavigation collapsed={false}/><Pixel/></>)
  await screen.findByText('Available')
  expect(titles()).toEqual(['Newer task', 'Older task'])
  expect(highlighted()).toEqual(['Newer task'])

  await act(async () => { fireEvent.click(rows()[1]) })

  // The chat itself opened: its first message shows outside the sidebar too.
  expect(screen.getAllByText('Older task').filter(node => !node.closest('.pixel-conversation-navigation'))).not.toHaveLength(0)
  expect(titles()).toEqual(['Newer task', 'Older task'])
  expect(highlighted()).toEqual(['Older task'])
  expect(Object.fromEntries(readConversations().map(item => [item.chatId, item.updatedAt]))).toEqual({older: 1000, newer: 2000})
})

it('editing the opened conversation still moves it to the top', async () => {
  render(<><PixelConversationNavigation collapsed={false}/><Pixel/></>)
  await screen.findByText('Available')
  await act(async () => { fireEvent.click(rows()[1]) })

  await act(async () => { fireEvent.change(screen.getByPlaceholderText(/^Message /), {target: {value: 'Next step'}}) })

  expect(titles()).toEqual(['Older task', 'Newer task'])
  expect(highlighted()).toEqual(['Older task'])
  expect(readConversations()[0]).toMatchObject({chatId: 'older', draft: 'Next step'})
})
