import {render, screen, fireEvent, waitFor, act} from '@testing-library/react'
import PixelMessageActions from './PixelMessageActions'

beforeEach(() => Object.defineProperty(navigator, 'clipboard', {configurable:true, get:() => undefined}))
afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers(); delete navigator.clipboard})
it('copies the exact original Markdown including fences, links and Unicode', async () => {
  const copy = vi.fn().mockResolvedValue(undefined)
  vi.spyOn(navigator, 'clipboard', 'get').mockReturnValue({writeText:copy})
  const content = '# Kết quả\n\n[Source](https://example.com)\n```py\nprint(42)\n```\n'
  render(<PixelMessageActions role="assistant" content={content}/>)
  fireEvent.click(screen.getByRole('button', {name:'Copy Markdown'}))
  await screen.findByText('Copied')
  expect(copy).toHaveBeenCalledWith(content)
  expect(screen.queryByRole('button', {name:'Reuse prompt'})).toBeNull()
})
it('offers selectable original text when clipboard is unavailable', async () => {
  vi.spyOn(navigator, 'clipboard', 'get').mockReturnValue(undefined)
  render(<PixelMessageActions role="assistant" content={'# Exact\n\n**text**'}/>)
  fireEvent.click(screen.getByRole('button', {name:'Copy Markdown'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Clipboard unavailable')
  fireEvent.click(screen.getByRole('button', {name:'Select original text'}))
  const field = screen.getByLabelText('Original message text')
  expect(field).toHaveValue('# Exact\n\n**text**')
  expect(field.selectionEnd).toBe(field.value.length)
})
it('reuses a user prompt only through the draft callback and honors its guard', () => {
  const reuse = vi.fn()
  const {rerender} = render(<PixelMessageActions role="user" content="Analyze this" canReuse onReuse={reuse}/>)
  fireEvent.click(screen.getByRole('button', {name:'Reuse prompt'}))
  expect(reuse).toHaveBeenCalledWith('Analyze this')
  rerender(<PixelMessageActions role="user" content="Analyze this" canReuse={false} onReuse={reuse}/>)
  expect(screen.getByRole('button', {name:'Reuse prompt'})).toBeDisabled()
})
it('does not transfer an old clipboard receipt to replaced message content', async () => {
  let finish
  vi.spyOn(navigator, 'clipboard', 'get').mockReturnValue({writeText:() => new Promise(resolve => {finish=resolve})})
  const {rerender} = render(<PixelMessageActions role="assistant" content="old"/>)
  fireEvent.click(screen.getByRole('button', {name:'Copy Markdown'}))
  rerender(<PixelMessageActions role="assistant" content="new"/>)
  await act(async () => finish())
  await waitFor(() => expect(screen.getByRole('button', {name:'Copy Markdown'})).toBeEnabled())
  expect(screen.queryByText('Copied')).toBeNull()
})
it('makes stalled clipboard permission requests recoverable', async () => {
  vi.useFakeTimers()
  vi.spyOn(navigator, 'clipboard', 'get').mockReturnValue({writeText:() => new Promise(() => {})})
  render(<PixelMessageActions role="assistant" content="Keep this"/>)
  fireEvent.click(screen.getByRole('button', {name:'Copy Markdown'}))
  await act(async () => vi.advanceTimersByTimeAsync(5001))
  expect(screen.getByLabelText('Original message text')).toHaveValue('Keep this')
})
