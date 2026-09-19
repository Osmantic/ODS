import {act, fireEvent, render as renderPlain, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import Pixel from './Pixel'
import PixelReplyCode from '../components/PixelReplyCode'

const message = 'Use this:\n\n```js\nconst greeting = "Xin chào 🌱"\nconsole.log(greeting)\n```\n\nInline `not a block`.'
const expected = 'const greeting = "Xin chào 🌱"\nconsole.log(greeting)\n'
const copy = vi.fn()
beforeEach(() => {
  localStorage.clear()
  copy.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:copy}})
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, json:async () => ({available:true, model:'pixel/default'})})))
})
afterEach(() => {vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()})
async function openReply() {
  localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({schema:1, chatId:'snippets', messages:[{role:'user', content:'Show code'}, {role:'assistant', content:message}]}))
  render(<Pixel/>)
  await screen.findByRole('button', {name:'Copy snippet'})
}
test('copies only the highlighted code from a restored conversation, keeping inline code unchanged', async () => {
  await openReply()
  fireEvent.click(screen.getByRole('button', {name:'Copy snippet'}))
  await screen.findByText('Snippet copied')
  expect(copy).toHaveBeenCalledExactlyOnceWith(expected)
  expect(screen.getAllByRole('button', {name:'Copy snippet'})).toHaveLength(1)
  expect(screen.getByLabelText('Reply code snippet').textContent).toBe(expected)
  expect(screen.getByLabelText('Reply code snippet')).toHaveAttribute('tabindex','0')
})
test('downloads one snippet as plain text without sending another chat or executing it', async () => {
  let blob, anchor
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL:vi.fn(value => {blob=value; return 'blob:snippet'}), revokeObjectURL:vi.fn()}))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {anchor=this})
  await openReply()
  const before = fetch.mock.calls.length
  fireEvent.click(screen.getByRole('button', {name:'Download snippet'}))
  const contents = await new Promise(resolve => {const reader = new globalThis.FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(blob)})
  expect(contents).toBe(expected)
  expect(blob.type).toBe('text/plain;charset=utf-8')
  expect(anchor.download).toBe('ods-reply-snippet.txt')
  expect(anchor.isConnected).toBe(false)
  expect(fetch).toHaveBeenCalledTimes(before)
})
const markdown = value => <ReactMarkdown rehypePlugins={[rehypeHighlight]} components={{pre:PixelReplyCode}}>{value}</ReactMarkdown>
test('discards old clipboard receipts when streaming replaces the displayed code', async () => {
  let resolve
  copy.mockImplementationOnce(() => new Promise(done => {resolve=done}))
  const view = renderPlain(markdown('```js\nold()\n```'))
  fireEvent.click(screen.getByRole('button', {name:'Copy snippet'}))
  view.rerender(markdown('```js\nnewer()\n```'))
  await act(async () => resolve())
  expect(screen.queryByText('Snippet copied')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Copy snippet'}))
  await screen.findByText('Snippet copied')
  expect(copy).toHaveBeenLastCalledWith('newer()\n')
})
test('bounds a blocked clipboard and allows an explicit retry without duplicating pending writes', async () => {
  vi.useFakeTimers()
  copy.mockImplementationOnce(() => new Promise(() => {}))
  renderPlain(markdown('```text\nexample\n```'))
  fireEvent.click(screen.getByRole('button', {name:'Copy snippet'}))
  expect(screen.getByRole('button', {name:'Copying snippet…'})).toBeDisabled()
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(screen.getByRole('alert')).toHaveTextContent('Clipboard unavailable')
  expect(copy).toHaveBeenCalledTimes(1)
  vi.useRealTimers()
  fireEvent.click(screen.getByRole('button', {name:'Copy snippet'}))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Snippet copied'))
})
test('keeps the latest download receipt when an earlier clipboard write settles', async () => {
  let resolve
  copy.mockImplementationOnce(() => new Promise(done => {resolve=done}))
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL:vi.fn(() => 'blob:snippet'), revokeObjectURL:vi.fn()}))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  renderPlain(markdown('```text\nexample\n```'))
  fireEvent.click(screen.getByRole('button', {name:'Copy snippet'}))
  fireEvent.click(screen.getByRole('button', {name:'Download snippet'}))
  await act(async () => resolve())
  expect(screen.getByRole('status')).toHaveTextContent('Text download started')
  expect(screen.getByRole('button', {name:'Copy snippet'})).toBeEnabled()
})
