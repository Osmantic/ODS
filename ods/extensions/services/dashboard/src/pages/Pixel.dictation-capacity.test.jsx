import {act, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

let speech
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({schema:1, chatId:'dictation-test', messages:[{role:'user', content:'Earlier prompt'}]}))
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
  speech = {start:vi.fn(), abort:vi.fn(), stop:vi.fn()}
  window.SpeechRecognition = function () {return speech}
})
afterEach(() => {delete window.SpeechRecognition; vi.restoreAllMocks(); vi.unstubAllGlobals()})
function final(text) {speech.onresult({resultIndex:0, results:[Object.assign([{transcript:text}], {isFinal:true})]})}
async function start(input) {
  render(<Pixel/>)
  await screen.findByText('Available')
  const field = screen.getByRole('textbox')
  fireEvent.change(field, {target:{value:input}})
  fireEvent.click(screen.getByRole('button', {name:'Dictate message'}))
  return field
}

test('keeps an overflowing final transcript recoverable without changing the current draft', async () => {
  const original = 'a'.repeat(16380)
  const field = await start(original)
  act(() => final('keep these words'))
  expect(field).toHaveValue(original)
  expect(screen.getByRole('textbox', {name:'Retained dictation'})).toHaveValue('keep these words ')
  expect(speech.abort).toHaveBeenCalledOnce()
  expect(screen.getByRole('button', {name:'Insert retained dictation'})).toBeDisabled()
  expect(screen.getByRole('button', {name:'Dictate message'})).toBeDisabled()
  act(() => final('late discarded result'))
  expect(screen.getByRole('textbox', {name:'Retained dictation'})).toHaveValue('keep these words ')
  fireEvent.change(field, {target:{value:'Short draft'}})
  fireEvent.click(screen.getByRole('button', {name:'Insert retained dictation'}))
  expect(field).toHaveValue('Short draft keep these words ')
  expect(screen.queryByRole('textbox', {name:'Retained dictation'})).not.toBeInTheDocument()
  // The context ring polls /api/pixel/chat/context on mount; retained dictation
  // must not trigger any state-changing POST (stream, cancel, agents).
  expect(fetch.mock.calls.some(([url,options]) => options?.method === 'POST' && url !== '/api/pixel/chat/context')).toBe(false)
})

test('counts back-to-back final events before render and preserves exact-fit text', async () => {
  const field = await start('x'.repeat(16381))
  act(() => {final('a'); final('b')})
  expect(field.value).toHaveLength(16384)
  expect(field.value.endsWith(' a ')).toBe(true)
  expect(screen.getByRole('textbox', {name:'Retained dictation'})).toHaveValue('b ')
  fireEvent.click(screen.getByRole('button', {name:'Discard retained dictation'}))
  expect(field.value).toHaveLength(16384)
  expect(screen.getByRole('button', {name:'Dictate message'})).toBeEnabled()
})

test('uses the latest edited draft when speech finishes and retains slash replacement', async () => {
  const field = await start('x'.repeat(16384))
  fireEvent.change(field, {target:{value:'/'}})
  act(() => final('Replace slash'))
  expect(field).toHaveValue('Replace slash ')
  fireEvent.change(field, {target:{value:'x'.repeat(16384)}})
  act(() => final('pending words'))
  expect(screen.getByRole('textbox', {name:'Retained dictation'})).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', {name:'New chat'}))
  await waitFor(() => expect(screen.queryByRole('textbox', {name:'Retained dictation'})).not.toBeInTheDocument())
  expect(field).toHaveValue('')
})
