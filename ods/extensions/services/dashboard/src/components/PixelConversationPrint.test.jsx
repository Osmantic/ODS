import {Blob as NodeBlob} from 'node:buffer'
import {fireEvent, render, screen} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {readConversations, saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('Blob', NodeBlob)
  vi.stubGlobal('URL', {createObjectURL:vi.fn(() => 'blob:print'), revokeObjectURL:vi.fn()})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()})
function open() {
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.contextMenu(screen.getByRole('button', {name:/^Print this/}))
}

test('downloads all saved messages as inert printable text without unsent drafts or runtime metadata', async () => {
  vi.useFakeTimers()
  const dangerous = '<script>alert(1)</script><img src="https://example.test/tracker"> & "Tiếng Việt"'
  const messages = [{role:'user', content:'Print this'}, ...Array.from({length:80}, (_, i) => ({role:i % 2 ? 'user' : 'assistant', content:`${i}: ${dangerous}`}))]
  saveConversation({schema:1, chatId:'print', messages, draft:'private unsent draft', inFlight:true, requestId:'private-runtime-id'})
  const before = localStorage.getItem('ods.pixel.conversations.v1')
  const select = vi.fn()
  window.addEventListener(SELECT_EVENT, select)
  try {
    open()
    fireEvent.click(screen.getByRole('menuitem', {name:'Download printable transcript'}))
    const blob = URL.createObjectURL.mock.calls[0][0]
    const html = await blob.text()
    const doc = new window.DOMParser().parseFromString(html, 'text/html')
    expect(blob.type).toBe('text/html;charset=utf-8')
    expect(doc.querySelectorAll('article')).toHaveLength(81)
    expect(doc.querySelectorAll('pre')[1].textContent).toBe(`0: ${dangerous}`)
    expect(doc.querySelectorAll('script,img,a,iframe,form')).toHaveLength(0)
    expect(doc.querySelector('meta[http-equiv="Content-Security-Policy"]').content).toContain("default-src 'none'")
    expect(doc.body.textContent).toContain('may be incomplete')
    expect(html).not.toContain('private unsent draft')
    expect(html).not.toContain('private-runtime-id')
    expect(select).not.toHaveBeenCalled()
    expect(localStorage.getItem('ods.pixel.conversations.v1')).toBe(before)
    const anchor = HTMLAnchorElement.prototype.click.mock.instances[0]
    expect(anchor.download).toBe('ods-pixel-transcript.html')
    expect(anchor.isConnected).toBe(false)
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:print')
  } finally {window.removeEventListener(SELECT_EVENT, select)}
})

test('reads the latest persisted messages when the menu action runs', async () => {
  saveConversation({schema:1, chatId:'print', messages:[{role:'user', content:'Print this'}]})
  open()
  const records = readConversations()
  records[0].messages.push({role:'assistant', content:'Saved after opening the menu'})
  localStorage.setItem('ods.pixel.conversations.v1', JSON.stringify(records))
  localStorage.setItem('ods.pixel.chat.v1', JSON.stringify(records[0]))
  fireEvent.click(screen.getByRole('menuitem', {name:'Download printable transcript'}))
  expect(await URL.createObjectURL.mock.calls[0][0].text()).toContain('Saved after opening the menu')
})

test('reports activation failure, cleans the resource and keeps history available for another attempt', () => {
  vi.useFakeTimers()
  saveConversation({schema:1, chatId:'print', messages:[{role:'user', content:'Print this'}]})
  open()
  HTMLAnchorElement.prototype.click.mockImplementationOnce(() => {throw new Error('blocked')})
  fireEvent.click(screen.getByRole('menuitem', {name:'Download printable transcript'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be downloaded')
  expect(readConversations()).toHaveLength(1)
  vi.advanceTimersByTime(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:print')
  fireEvent.contextMenu(screen.getByRole('button', {name:/^Print this/}))
  fireEvent.click(screen.getByRole('menuitem', {name:'Download printable transcript'}))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('does not leak an unsent-only draft through the automatic document title', async () => {
  saveConversation({schema:1, chatId:'draft-only', messages:[], draft:'Private unfinished thought'})
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.contextMenu(screen.getByRole('button', {name:'Private unfinished thought',exact:true}))
  fireEvent.click(screen.getByRole('menuitem', {name:'Download printable transcript'}))
  const html = await URL.createObjectURL.mock.calls[0][0].text()
  expect(html).not.toContain('Private unfinished thought')
  expect(html).toContain('<title>Untitled conversation</title>')
})
