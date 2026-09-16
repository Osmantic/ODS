import {act, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {SEND_KEY_STORAGE} from '../lib/usePixelSendKey'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
  HTMLDialogElement.prototype.showModal = function () {this.open = true}
  HTMLDialogElement.prototype.close = function () {this.open = false; this.dispatchEvent(new Event('close'))}
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals(); delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close})

test.each(['enter','mod-enter'])('shows the current %s preference from the real composer without changing or sending the draft', async mode => {
  localStorage.setItem(SEND_KEY_STORAGE, mode)
  render(<Pixel/>)
  await screen.findByText('Available')
  const composer = screen.getByRole('textbox')
  fireEvent.change(composer, {target:{value:'Keep this unsent'}})
  const trigger = screen.getByRole('button', {name:'Keyboard shortcuts',exact:true})
  fireEvent.click(trigger)
  const help = screen.getByRole('dialog', {name:'Pixel keyboard shortcuts'})
  expect(within(help).getByRole('row', {name:/Send a message/})).toHaveTextContent(mode === 'enter' ? 'Enter' : 'Ctrl/⌘ + Enter')
  expect(within(help).getByRole('row', {name:/Search conversations/})).toHaveTextContent('Ctrl/⌘ + K')
  expect(screen.getByRole('button', {name:'Close keyboard shortcuts'})).toHaveFocus()
  expect(composer).toHaveValue('Keep this unsent')
  expect(fetch.mock.calls.some(([,options]) => options?.method === 'POST')).toBe(false)
  fireEvent.click(screen.getByRole('button', {name:'Close keyboard shortcuts'}))
  expect(trigger).toHaveFocus()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('refreshes help after a cross-tab preference change and restores focus after native dismissal', async () => {
  render(<Pixel/>)
  await screen.findByText('Available')
  const trigger = screen.getByRole('button', {name:'Keyboard shortcuts',exact:true})
  fireEvent.click(trigger)
  const help = screen.getByRole('dialog', {name:'Pixel keyboard shortcuts'})
  localStorage.setItem(SEND_KEY_STORAGE, 'mod-enter')
  fireEvent(window, new StorageEvent('storage', {key:SEND_KEY_STORAGE}))
  expect(within(help).getByRole('row', {name:/Send a message/})).toHaveTextContent('Ctrl/⌘ + Enter')
  act(() => help.close())
  expect(trigger).toHaveFocus()
})
