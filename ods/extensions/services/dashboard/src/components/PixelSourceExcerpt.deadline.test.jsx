import {act, fireEvent, render, screen} from '@testing-library/react'
import PixelSourceExcerpt from './PixelSourceExcerpt'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

it('releases a stalled clipboard write and keeps late results from confirming a newer range', async () => {
  vi.useFakeTimers()
  let finishOld
  const writeText = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve })).mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText}})
  render(<PixelSourceExcerpt source={'first\nsecond\n'}/> )
  fireEvent.click(screen.getByText('Extract lines'))
  fireEvent.click(screen.getByRole('button', {name:'Copy excerpt'}))
  expect(screen.getByRole('button', {name:'Copying excerpt...'})).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByRole('button', {name:'Copy excerpt'})).toBeEnabled()
  expect(screen.getByRole('alert')).toHaveTextContent('copy manually')
  expect(screen.getByLabelText('Selected source excerpt')).toHaveValue('first\nsecond\n')
  fireEvent.change(screen.getByLabelText('Start line'), {target:{value:'2'}})
  await act(async () => { finishOld() })
  expect(screen.queryByText('Excerpt copied.')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Copy excerpt'}))
  await act(async () => {})
  expect(screen.getByRole('status')).toHaveTextContent('Excerpt copied.')
  expect(writeText.mock.calls).toEqual([['first\nsecond\n'], ['second\n']])
  expect(vi.getTimerCount()).toBe(0)
})

it.each(['resolve', 'reject', 'range', 'unmount'])('clears the clipboard deadline on %s', async action => {
  vi.useFakeTimers()
  let resolve, reject
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:vi.fn(() => new Promise((done, fail) => {resolve=done;reject=fail}))}})
  const view=render(<PixelSourceExcerpt source={'first\nsecond\n'}/> )
  fireEvent.click(screen.getByText('Extract lines'))
  // The browser queues a details-toggle event independently of clipboard work.
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  fireEvent.click(screen.getByRole('button', {name:'Copy excerpt'}))
  expect(vi.getTimerCount()).toBe(1)
  await act(async () => {
    if(action==='resolve')resolve()
    if(action==='reject')reject(new Error('denied'))
    if(action==='range')fireEvent.change(screen.getByLabelText('Start line'), {target:{value:'2'}})
    if(action==='unmount')view.unmount()
  })
  expect(vi.getTimerCount()).toBe(0)
})
