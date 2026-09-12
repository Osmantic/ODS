import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

beforeEach(() => {
  vi.useFakeTimers()
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
})
afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.unstubAllGlobals()
  delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close
})

test('focuses artifact inspection, cancels without importing and restores the repository button', async () => {
  const fetchMock = vi.fn(async url => ({ ok: true, json: async () => url.includes('/search?')
    ? { models: [{ id: 'author/model', author: 'author' }] }
    : { id: 'author/model', artifacts: [], url: 'https://huggingface.co/author/model' } }))
  vi.stubGlobal('fetch', fetchMock)
  render(<HuggingFaceModelBrowser />)
  await act(async () => vi.advanceTimersByTimeAsync(350))
  const trigger = screen.getByRole('button', { name: 'Choose file' })
  trigger.focus()
  await act(async () => fireEvent.click(trigger))
  const dialog = screen.getByRole('dialog')
  expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
  fireEvent(dialog, new Event('cancel', { cancelable: true }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(trigger).toHaveFocus()
  expect(fetchMock.mock.calls.every(([url]) => !url.endsWith('/import'))).toBe(true)
})
