import { fireEvent, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../test/test-utils'
import PortalChatMemory from './PortalChatMemory'

const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear() })

it('marks a chat not to be remembered, and shows it again for that chat only', async () => {
  const fetchMock = vi.fn(async () => response({}))
  vi.stubGlobal('fetch', fetchMock)
  const { rerender } = render(createElement(PortalChatMemory, { chatId: 'chat-1' }))
  const box = screen.getByRole('switch', { name: 'Don’t remember this chat' })
  expect(box).not.toBeChecked()
  // Checkboxes in the chat are Portal's answers (task lists); this one is a switch.
  expect(screen.queryByRole('checkbox')).toBeNull()
  fireEvent.click(box)
  await waitFor(() => expect(box).toBeChecked())
  expect(fetchMock).toHaveBeenCalledWith('/api/pixel/integrations/memory-chat', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ chatId: 'chat-1', remember: false }) }))
  rerender(createElement(PortalChatMemory, { chatId: 'chat-2' }))
  expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).not.toBeChecked()
  rerender(createElement(PortalChatMemory, { chatId: 'chat-1' }))
  expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).toBeChecked()
  fireEvent.click(screen.getByRole('switch', { name: 'Don’t remember this chat' }))
  await waitFor(() => expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).not.toBeChecked())
  expect(fetchMock).toHaveBeenLastCalledWith('/api/pixel/integrations/memory-chat', expect.objectContaining({ body: JSON.stringify({ chatId: 'chat-1', remember: true }) }))
})

it('a refusal keeps the old choice and says why', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ detail: 'Portal integrations are unavailable.' }, 503)))
  render(createElement(PortalChatMemory, { chatId: 'chat-1' }))
  fireEvent.click(screen.getByRole('switch', { name: 'Don’t remember this chat' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal integrations are unavailable.')
  expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).not.toBeChecked()
})

it('works when the browser refuses storage', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({})))
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  render(createElement(PortalChatMemory, { chatId: 'chat-1' }))
  fireEvent.click(screen.getByRole('switch', { name: 'Don’t remember this chat' }))
  await waitFor(() => expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).toBeChecked())
})

it('an answer for a chat the owner has left is kept for that chat, not shown on the new one', async () => {
  let release
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { release = () => resolve(response({})) })))
  const { rerender } = render(createElement(PortalChatMemory, { chatId: 'chat-1' }))
  fireEvent.click(screen.getByRole('switch', { name: 'Don’t remember this chat' }))
  rerender(createElement(PortalChatMemory, { chatId: 'chat-2' }))
  release()
  await waitFor(() => expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).toBeEnabled())
  expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).not.toBeChecked()
  rerender(createElement(PortalChatMemory, { chatId: 'chat-1' }))
  expect(screen.getByRole('switch', { name: 'Don’t remember this chat' })).toBeChecked()
})
