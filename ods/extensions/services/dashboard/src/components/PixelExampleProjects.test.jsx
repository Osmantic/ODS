import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {saveConversation} from '../lib/pixelConversations'
import {EXAMPLE_OPEN_EVENT, EXAMPLE_REFRESH_EVENT} from '../lib/exampleProjects'

const listed = projects => ({ok: true, status: 200, json: async () => ({schemaVersion: 1, projects})})
function receipt(relativeDirectory, digit = 'a') {
  const sha256 = digit.repeat(64), siteId = `site-${sha256.slice(0, 24)}`
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', relativeDirectory, siteId, sha256,
    entrySha256: 'f'.repeat(64), files: 9, bytes: 243500, port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`}
}
function shape(element) {
  return [element.tagName, element.className, ...[...element.children].map(child => shape(child).join('>'))]
}

beforeEach(() => localStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear() })

test('a present shipped example sits in Projects > Playground exactly like a project made in Portal', async () => {
  saveConversation({schema: 1, chatId: 'snake', messages: [{role: 'user', content: 'Build a snake game'}], preview: receipt('Playground/snake-game', 'b')})
  vi.stubGlobal('fetch', vi.fn(async () => listed(['elf-of-doom'])))
  const opened = vi.fn()
  window.addEventListener(EXAMPLE_OPEN_EVENT, opened)
  render(<PixelConversationNavigation collapsed={false}/>)
  const example = await screen.findByTitle('Playground/elf-of-doom')
  const project = screen.getByTitle('Playground/snake-game')
  expect(fetch).toHaveBeenCalledWith('/api/pixel/example-projects', expect.objectContaining({cache: 'no-store'}))
  // Same place, same markup: no badge, chip, button or other marker.
  expect(shape(example)).toEqual(shape(project))
  expect(example.closest('details').className).toBe(project.closest('details').className)
  expect(example.closest('details.rail-playground')).toBe(project.closest('details.rail-playground'))
  expect(example.textContent).toBe('elf-of-doom')
  fireEvent.click(example)
  expect(opened).toHaveBeenCalledTimes(1)
  expect(opened.mock.calls[0][0].detail).toEqual({name: 'elf-of-doom'})
  expect(example.closest('details').open).toBe(false)
  window.removeEventListener(EXAMPLE_OPEN_EVENT, opened)
})

test('without the folder, or without an answer, there is no example and no empty Projects section', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => listed([])))
  const first = render(<PixelConversationNavigation collapsed={false}/>)
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect(screen.queryByText('Projects')).toBeNull()
  first.unmount()
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
  render(<PixelConversationNavigation collapsed={false}/>)
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect(screen.queryByText('Projects')).toBeNull()
  expect(screen.queryByTitle('Playground/elf-of-doom')).toBeNull()
})

test('a deleted or renamed folder quietly leaves the list on the next check', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => listed(['elf-of-doom'])))
  render(<PixelConversationNavigation collapsed={false}/>)
  await screen.findByTitle('Playground/elf-of-doom')
  fetch.mockResolvedValue(listed([]))
  act(() => { window.dispatchEvent(new Event(EXAMPLE_REFRESH_EVENT)) })
  await waitFor(() => expect(screen.queryByTitle('Playground/elf-of-doom')).toBeNull())
  fetch.mockResolvedValue(listed(['elf-of-doom']))
  act(() => { window.dispatchEvent(new Event('focus')) })
  expect(await screen.findByTitle('Playground/elf-of-doom')).toBeVisible()
})

test('once the owner has conversations in the example folder it is that one project row', async () => {
  saveConversation({schema: 1, chatId: 'elf', messages: [{role: 'user', content: 'In elf-of-doom, make the arrows give 7'}], preview: receipt('Playground/elf-of-doom')})
  vi.stubGlobal('fetch', vi.fn(async () => listed(['elf-of-doom'])))
  const opened = vi.fn()
  window.addEventListener(EXAMPLE_OPEN_EVENT, opened)
  render(<PixelConversationNavigation collapsed={false}/>)
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect(screen.getAllByTitle('Playground/elf-of-doom')).toHaveLength(1)
  expect(screen.getByRole('button', {name: 'In elf-of-doom, make the arrows give 7', exact: true})).toBeInTheDocument()
  // Its folder row folds and unfolds the conversations like any project.
  const folder = screen.getByTitle('Playground/elf-of-doom').closest('details')
  const wasOpen = folder.open
  fireEvent.click(screen.getByTitle('Playground/elf-of-doom'))
  expect(folder.open).toBe(!wasOpen)
  expect(opened).not.toHaveBeenCalled()
  window.removeEventListener(EXAMPLE_OPEN_EVENT, opened)
})
