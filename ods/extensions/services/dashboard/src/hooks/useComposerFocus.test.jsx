import { useRef, useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useComposerFocus } from './useComposerFocus'

function blurDisabled(field) {
  field.disabled = false
  field.blur()
  field.disabled = true
}

function Composer({ disabled = false, overlay = false, notice = false, visible = true }) {
  const inputRef = useRef(null)
  const [value, setValue] = useState('')
  const prepareSend = useComposerFocus({ inputRef, disabled, visible, onType: (key, start, end) => setValue(text => {
    const from = Math.min(start ?? text.length, text.length)
    const to = Math.min(end ?? from, text.length)
    return text.slice(0, from) + key + text.slice(to)
  }) })
  return <>
    <div style={visible ? undefined : {display:'none'}}>
    <textarea aria-label="Message" ref={inputRef} disabled={disabled} value={value} onChange={event => setValue(event.target.value)}/>
    </div>
    <button onClick={prepareSend}>Send</button>
    <input aria-label="Search"/>
    <a href="#other">Other page</a>
    <div contentEditable suppressContentEditableWarning data-testid="editor"/>
    <p>Selected answer</p>
    {overlay && <div role="dialog" aria-label="Settings">Settings</div>}
    {notice && <div role="dialog" aria-label="Install prompt" data-composer-focus-ignore>Install</div>}
  </>
}

afterEach(() => { window.getSelection()?.removeAllRanges(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('focuses when initially available and restores after a disabled turn', () => {
  const { rerender } = render(<Composer disabled/>)
  expect(screen.getByRole('textbox', {name:'Message'})).not.toHaveFocus()
  rerender(<Composer/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  expect(field).toHaveFocus()
  rerender(<Composer disabled/>)
  blurDisabled(field) // Browsers drop focus from disabled controls; jsdom does not.
  rerender(<Composer/>)
  expect(field).toHaveFocus()
})

it('restores after clicking Send, including the first send on a touch device', async () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({matches:true})))
  const user = userEvent.setup()
  const {rerender} = render(<Composer/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  expect(field).not.toHaveFocus()
  await user.type(field, 'Hello')
  await user.click(screen.getByRole('button', {name:'Send'}))
  expect(field).toHaveFocus()
  rerender(<Composer disabled/>)
  blurDisabled(field)
  rerender(<Composer/>)
  expect(field).toHaveFocus()
})

it('does not take focus from another input during startup or when a turn ends', async () => {
  const user = userEvent.setup()
  const {rerender} = render(<Composer disabled/>)
  const search = screen.getByRole('textbox', {name:'Search'})
  await user.click(search)
  rerender(<Composer/>)
  expect(search).toHaveFocus()
  await user.click(screen.getByRole('textbox', {name:'Message'}))
  rerender(<Composer disabled/>)
  await user.click(search)
  rerender(<Composer/>)
  expect(search).toHaveFocus()
})

it('lets background typing continue the draft without losing or duplicating the first character', async () => {
  const user = userEvent.setup()
  render(<Composer/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  await user.keyboard('First ')
  field.blur()
  await user.keyboard('second')
  expect(field).toHaveValue('First second')
  expect(field).toHaveFocus()
})

it.each(['Search', 'Other page', 'editor'])('leaves keyboard input with %s', async name => {
  const user = userEvent.setup()
  render(<Composer/>)
  const target = name === 'editor' ? screen.getByTestId('editor') : name === 'Search'
    ? screen.getByRole('textbox', {name}) : screen.getByRole('link', {name})
  await user.click(target)
  await user.keyboard('hello')
  expect(screen.getByRole('textbox', {name:'Message'})).toHaveValue('')
  expect(target).toHaveFocus()
})

it.each([
  {key:'k', ctrlKey:true}, {key:'k', metaKey:true}, {key:'k', altKey:true},
  {key:'Enter'}, {key:'Backspace'}, {key:'ArrowDown'}, {key:'Process', isComposing:true},
  {key:'a', isComposing:true}, {key:'a', keyCode:229},
])('does not intercept shortcuts/navigation/composition: %j', key => {
  render(<Composer/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  field.blur()
  expect(fireEvent.keyDown(document.body, key)).toBe(true)
  expect(field).toHaveValue('')
  expect(field).not.toHaveFocus()
})

it('does not capture typing or restore focus while a dialog is open', async () => {
  const user = userEvent.setup()
  const {rerender} = render(<Composer disabled/>)
  rerender(<Composer overlay/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  expect(field).not.toHaveFocus()
  await user.keyboard('hello')
  expect(field).toHaveValue('')
})

it('keeps restoring focus and capturing typing while a non-blocking notice is shown', async () => {
  const user = userEvent.setup()
  const {rerender} = render(<Composer disabled notice/>)
  rerender(<Composer notice/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  expect(field).toHaveFocus()
  field.blur()
  await user.keyboard('hello')
  expect(field).toHaveValue('hello')
  expect(field).toHaveFocus()
})

it('does not steal selected message text or restore after keyboard navigation/window blur', () => {
  const {rerender} = render(<Composer/>)
  const field = screen.getByRole('textbox', {name:'Message'})
  field.blur()
  const range = document.createRange()
  range.selectNodeContents(screen.getByText('Selected answer'))
  window.getSelection().addRange(range)
  fireEvent.keyDown(document.body, {key:'x'})
  expect(field).toHaveValue('')
  window.getSelection().removeAllRanges()
  for (const relinquish of [() => fireEvent.keyDown(document.body, {key:'Tab'}), () => fireEvent(window, new Event('blur'))]) {
    field.focus()
    rerender(<Composer disabled/>)
    blurDisabled(field)
    relinquish()
    rerender(<Composer/>)
    expect(field).not.toHaveFocus()
  }
})

it('removes the global keyboard listener when the conversation unmounts', () => {
  const {unmount} = render(<Composer/>)
  unmount()
  expect(fireEvent.keyDown(document.body, {key:'x'})).toBe(true)
})

it('does not intercept Space or mutate the draft when the composer is hidden', () => {
  render(<Composer visible={false}/>)
  const field = screen.getByLabelText('Message')
  field.blur()
  expect(fireEvent.keyDown(document.body, {key:' '})).toBe(true)
  expect(field).toHaveValue('')
  expect(field).not.toHaveFocus()
})

it('inserts background typing at the caret instead of appending', async () => {
  const user = userEvent.setup()
  render(<Composer/>)
  const field = screen.getByLabelText('Message')
  await user.type(field, 'abcd')
  field.setSelectionRange(2, 2)
  field.blur()
  await user.keyboard('XY')
  expect(field).toHaveValue('abXYcd')
  expect(field).toHaveFocus()
  expect(field.selectionStart).toBe(4)
  expect(field.selectionEnd).toBe(4)
})

it('leaves typing with the page when a composer ancestor is hidden', () => {
  render(<div hidden><Composer/></div>)
  const field = screen.getByLabelText('Message')
  expect(fireEvent.keyDown(document.body, {key:'x'})).toBe(true)
  expect(field).toHaveValue('')
  expect(field).not.toHaveFocus()
})
