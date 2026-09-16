import {Blob as NodeBlob} from 'node:buffer'
import {act, cleanup, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {saveConversation} from '../lib/pixelConversations'
import {parseConversationImport} from '../lib/pixelConversationImport'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('Blob',NodeBlob)
  vi.stubGlobal('URL',{createObjectURL:vi.fn(() => 'blob:recovery'),revokeObjectURL:vi.fn()})
  vi.spyOn(window.HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
})
afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers()})

it('downloads the live unsaved draft and complete message text when storage writes fail', async () => {
  saveConversation({schema:1,chatId:'recover-me',messages:[{role:'user',content:'Original question'},{role:'assistant',content:'Retained answer',status:'done'}],draft:'Old saved draft'})
  const before = localStorage.getItem('ods.pixel.conversations.v1')
  const original = window.Storage.prototype.setItem
  vi.spyOn(window.Storage.prototype,'setItem').mockImplementation(function (key,value) {
    if (key.startsWith('ods.pixel.')) throw new globalThis.DOMException('Full','QuotaExceededError')
    return original.call(this,key,value)
  })
  render(<Pixel/>)
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText(/^Message .+\.\.\.$/),{target:{value:'Newest unsaved draft'}})
  await screen.findByText(/Your browser could not save this conversation/)
  vi.useFakeTimers()
  fireEvent.click(screen.getByRole('button',{name:'Download recovery copy'}))
  const archive = JSON.parse(await URL.createObjectURL.mock.calls[0][0].text())
  const restored = parseConversationImport(archive)
  expect(restored.draft).toBe('Newest unsaved draft')
  expect(restored.messages.map(message => message.content)).toEqual(['Original question','Retained answer'])
  expect(restored.inFlight).toBe(false)
  expect(restored.preview).toBeNull()
  expect(localStorage.getItem('ods.pixel.conversations.v1')).toBe(before)
  expect(fetch.mock.calls.every(([,options]) => !options?.method || options.method === 'GET')).toBe(true)
  expect(window.HTMLAnchorElement.prototype.click.mock.instances[0].isConnected).toBe(false)
  await act(async () => {await vi.advanceTimersByTimeAsync(1000)})
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:recovery')
})

it('reports download failure without dismissing the storage warning or losing the draft', async () => {
  render(<Pixel/>)
  await screen.findByText('Available')
  vi.spyOn(window.Storage.prototype,'setItem').mockImplementation(() => {throw new globalThis.DOMException('Full','QuotaExceededError')})
  fireEvent.change(screen.getByPlaceholderText(/^Message .+\.\.\.$/),{target:{value:'Keep this draft'}})
  await waitFor(() => expect(screen.getByText(/Your browser could not save this conversation/)).toBeVisible())
  URL.createObjectURL.mockImplementation(() => {throw new Error('Download unavailable')})
  await act(async () => fireEvent.click(screen.getByRole('button',{name:'Download recovery copy'})))
  expect(screen.getByText(/Recovery download could not start/)).toBeVisible()
  expect(screen.getByPlaceholderText(/^Message .+\.\.\.$/)).toHaveValue('Keep this draft')
})


it('requests browser leave confirmation only while live text cannot be saved', async () => {
  const beforeUnload = () => {
    const event = new Event('beforeunload', {cancelable:true})
    window.dispatchEvent(event)
    return event.defaultPrevented
  }
  const {unmount} = render(<Pixel/>)
  await screen.findByText('Available')
  const input = screen.getByPlaceholderText(/^Message .+\.\.\.$/)
  fireEvent.change(input, {target:{value:'Saved draft'}})
  expect(beforeUnload()).toBe(false)
  const failure = vi.spyOn(window.Storage.prototype,'setItem').mockImplementation(() => {throw new globalThis.DOMException('Full','QuotaExceededError')})
  fireEvent.change(input, {target:{value:'Only in this tab'}})
  await screen.findByText(/Your browser could not save this conversation/)
  expect(beforeUnload()).toBe(true)
  // A started download does not prove that a file was saved to disk.
  fireEvent.click(screen.getByRole('button',{name:'Download recovery copy'}))
  expect(beforeUnload()).toBe(true)
  failure.mockRestore()
  fireEvent.change(input, {target:{value:'Storage works again'}})
  await waitFor(() => expect(screen.queryByText(/Your browser could not save this conversation/)).not.toBeInTheDocument())
  expect(beforeUnload()).toBe(false)
  unmount()
  expect(beforeUnload()).toBe(false)
})
