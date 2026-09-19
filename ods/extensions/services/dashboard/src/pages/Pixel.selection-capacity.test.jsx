import {fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

let selected
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({schema:1, chatId:'selection-capacity', messages:[{role:'user', content:'Explain this'}, {role:'assistant', content:'Select a passage'}]}))
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
  selected = 'exact passage'
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals()})
async function showSelection(input) {
  render(<Pixel/>)
  await screen.findByText('Available')
  const composer = screen.getByRole('textbox')
  fireEvent.change(composer, {target:{value:input}})
  const node = screen.getByText('Select a passage').firstChild
  vi.spyOn(window, 'getSelection').mockReturnValue({rangeCount:1,isCollapsed:false,toString:() => selected,removeAllRanges:vi.fn(),getRangeAt:() => ({startContainer:node,endContainer:node,getBoundingClientRect:() => ({left:100,top:100})})})
  fireEvent.mouseUp(document)
  return composer
}
test('keeps a full existing draft unchanged and disables overflowing selection actions', async () => {
  const original = 'a'.repeat(16383)
  const composer = await showSelection(original)
  const toolbar = within(screen.getByRole('toolbar'))
  for (const button of toolbar.getAllByRole('button')) expect(button).toBeDisabled()
  fireEvent.click(toolbar.getByRole('button', {name:'Explain'}))
  expect(composer).toHaveValue(original)
  expect(toolbar.getByRole('status')).toHaveTextContent('exceed the draft limit')
  expect(window.getSelection().removeAllRanges).not.toHaveBeenCalled()
})
test('accepts an exact fit including wrapper and separator while disabling longer actions', async () => {
  const addition = `Explain this passage:\n\n“${selected}”\n\n`
  const original = 'a'.repeat(16384 - addition.length - 1)
  const composer = await showSelection(original)
  const toolbar = within(screen.getByRole('toolbar'))
  expect(toolbar.getByRole('button', {name:'Tone'})).toBeDisabled()
  expect(toolbar.getByRole('button', {name:'Explain'})).toBeEnabled()
  fireEvent.click(toolbar.getByRole('button', {name:'Explain'}))
  expect(composer.value).toBe(original+' '+addition)
  expect(composer.value).toHaveLength(16384)
})
test('recomputes capacity after editing and preserves slash-command replacement', async () => {
  const composer = await showSelection('a'.repeat(16384))
  fireEvent.change(composer, {target:{value:'/'}})
  expect(screen.getByRole('button', {name:'Explain',exact:true})).toBeEnabled()
  fireEvent.click(screen.getByRole('button', {name:'Explain',exact:true}))
  expect(composer.value).toBe(`Explain this passage:\n\n“${selected}”\n\n`)
  expect(composer).toHaveFocus()
})
