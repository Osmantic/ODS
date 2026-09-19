import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { DependencyConfirmDialog } from './DependencyBadges'

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
})
afterEach(() => { cleanup(); delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close })

test('opens a native modal, cancels with Escape and restores the invoking control', () => {
  const trigger = document.createElement('button')
  document.body.append(trigger)
  trigger.focus()
  const onConfirm = vi.fn(), onCancel = vi.fn()
  const view = render(<DependencyConfirmDialog ext={{ name: 'Voice service' }} missingDeps={['tts']} onConfirm={onConfirm} onCancel={onCancel} />)
  const dialog = screen.getByRole('dialog', { name: 'Enable dependencies' })
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  fireEvent(dialog, new Event('cancel', { cancelable: true }))
  expect(onCancel).toHaveBeenCalledOnce()
  expect(onConfirm).not.toHaveBeenCalled()
  expect(dialog.tagName).toBe('DIALOG')
  expect(dialog).toHaveAttribute('open')
  view.unmount()
  expect(trigger).toHaveFocus()
  trigger.remove()
})

test('only explicit Enable All accepts the displayed dependency list', () => {
  const onConfirm = vi.fn(), onCancel = vi.fn()
  render(<DependencyConfirmDialog ext={{ name: 'Voice service' }} missingDeps={['tts', 'whisper']} onConfirm={onConfirm} onCancel={onCancel} />)
  fireEvent.click(screen.getByText('whisper'))
  expect(onConfirm).not.toHaveBeenCalled()
  expect(onCancel).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Enable All' }))
  expect(onConfirm).toHaveBeenCalledOnce()
})
