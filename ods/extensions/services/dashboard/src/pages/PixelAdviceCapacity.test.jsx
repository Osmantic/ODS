import {fireEvent, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

const id = '5c292c25-9368-4d9a-83cd-1e14d34cb128'
const prefix = 'Advisory response (untrusted; evaluate before acting):\n'
let answer

beforeEach(() => {
  // jsdom does not implement the native-dialog API used by the parallel modal PR.
  Object.defineProperty(globalThis.HTMLDialogElement.prototype, 'showModal', {configurable:true, value:vi.fn(function () { this.setAttribute('open', '') })})
  Object.defineProperty(globalThis.HTMLDialogElement.prototype, 'close', {configurable:true, value:vi.fn(function () { this.removeAttribute('open') })})
  answer = 'Retain the complete answer.'
  localStorage.clear()
  localStorage.setItem('ods.pixel.advice.job.v1', id)
  vi.stubGlobal('fetch', vi.fn(async url => ({ok:true, json:async () => {
    if (url === '/api/pixel/advice/status') return {jobId:id,status:'completed',result:{text:answer,usage:{}}}
    return {available:true,model:'pixel/default'}
  }})))
})
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear()})

async function openAdvice(draft) {
  render(<Pixel/>);
  await screen.findByText('Available')
  const composer = screen.getByPlaceholderText('Message Portal...')
  fireEvent.change(composer, {target:{value:draft}})
  fireEvent.click(screen.getByLabelText('Chat options'))
  fireEvent.click(screen.getByText('Advanced tools'))
  fireEvent.click(screen.getByRole('button', {name:/^Ask for advice/}))
  await screen.findByText('Advice: completed')
  return {composer, paste:screen.getByRole('button', {name:'Paste advice into composer (does not send)'})}
}

it('preserves the draft and answer when their combined text exceeds composer capacity', async () => {
  const draft = 'd'.repeat(16384 - prefix.length - answer.length - 1)
  const {composer, paste} = await openAdvice(draft)
  expect(paste).toBeDisabled()
  expect(screen.getByText(/Shorten the draft or copy an excerpt/)).toBeVisible()
  fireEvent.click(paste)
  expect(composer).toHaveValue(draft)
  expect(screen.getByText(answer)).toBeVisible()
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat')).toBe(false)
})

it.each(['', '\n'])('inserts an exact-fit answer with the actual separator for %j', async whitespace => {
  const separator = 2
  const draft = 'd'.repeat(16384 - prefix.length - answer.length - whitespace.length - separator) + whitespace
  const {composer, paste} = await openAdvice(draft)
  expect(paste).toBeEnabled()
  fireEvent.click(paste)
  expect(composer.value).toBe(draft + (separator ? '\n\n' : '') + prefix + answer)
  expect(composer.value).toHaveLength(16384)
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat')).toBe(false)
})

it('keeps an oversized answer readable without inserting a truncated version', async () => {
  answer = 'A'.repeat(16384)
  const {composer, paste} = await openAdvice('')
  expect(paste).toBeDisabled()
  expect(screen.getByText(answer)).toBeVisible()
  expect(composer).toHaveValue('')
})
