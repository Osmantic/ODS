
import {Blob as NodeBlob} from 'node:buffer'
import {render,screen,fireEvent} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {saveConversation,readConversations,SELECT_EVENT} from '../lib/pixelConversations'
beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('Blob',NodeBlob)
  vi.stubGlobal('URL',{createObjectURL:vi.fn(() => 'blob:local-export'),revokeObjectURL:vi.fn()})
  vi.spyOn(window.HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers()})
test('exports complete retained history and metadata without selecting or truncating it',async () => {
  vi.useFakeTimers()
  const messages=Array.from({length:80},(_,index) => ({role:index%2 ? 'assistant' : 'user',content:'Turn '+index}))
  saveConversation({schema:1,chatId:'export-test',messages,draft:'Unsent draft',inFlight:true,requestId:'retained-request'})
  const chat=readConversations()[0]
  const before=localStorage.getItem('ods.pixel.conversations.v1')
  const select=vi.fn()
  window.addEventListener(SELECT_EVENT,select)
  try {
    render(<PixelConversationNavigation collapsed={false}/>)
    fireEvent.contextMenu(screen.getByRole('button',{name:/^Turn 0/}))
    fireEvent.click(screen.getByRole('menuitem',{name:'Export conversation'}))
    expect(URL.createObjectURL).toHaveBeenCalledOnce()
    const blob=URL.createObjectURL.mock.calls[0][0]
    expect(blob.type).toBe('application/json')
    const exported=JSON.parse(await blob.text())
    expect(exported.kind).toBe('ods-pixel-conversation')
    expect(exported.schemaVersion).toBe(1)
    expect(exported.conversation).toEqual(chat)
    expect(exported.conversation.messages).toHaveLength(80)
    const anchor=window.HTMLAnchorElement.prototype.click.mock.instances[0]
    expect(anchor.download).toBe('ods-pixel-export-test.json')
    expect(anchor.href).toBe('blob:local-export')
    expect(anchor.isConnected).toBe(false)
    expect(select).not.toHaveBeenCalled()
    expect(localStorage.getItem('ods.pixel.conversations.v1')).toBe(before)
    vi.advanceTimersByTime(1000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-export')
  } finally {window.removeEventListener(SELECT_EVENT,select)}
})
test('reports download failure while preserving the conversation', () => {
  saveConversation({schema:1,chatId:'export-test',messages:[{role:'user',content:'Keep me'}]})
  URL.createObjectURL.mockImplementation(() => {throw new Error('Unavailable')})
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.contextMenu(screen.getByRole('button',{name:'Keep me',exact:true}))
  fireEvent.click(screen.getByRole('menuitem',{name:'Export conversation'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be exported')
  expect(readConversations()).toHaveLength(1)
  expect(window.HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})
