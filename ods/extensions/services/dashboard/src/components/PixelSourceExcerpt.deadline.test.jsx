import {act, fireEvent, render, screen} from '@testing-library/react'
import PixelSourceExcerpt from './PixelSourceExcerpt'

beforeEach(() => vi.useFakeTimers())
afterEach(() => {vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()})

it('releases a stalled copy and permits a new copy without accepting the late receipt', async () => {
  let finishOld
  const writeText = vi.fn().mockImplementationOnce(() => new Promise(resolve => {finishOld=resolve}))
    .mockResolvedValueOnce(undefined)
  vi.stubGlobal('navigator', {clipboard:{writeText}})
  render(<PixelSourceExcerpt source={'first\nsecond\n'}/>)
  fireEvent.click(screen.getByText('Extract lines'))
  fireEvent.click(screen.getByRole('button', {name:'Copy excerpt'}))
  expect(screen.getByRole('button', {name:'Copying excerpt...'})).toBeDisabled()
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(screen.getByRole('alert')).toHaveTextContent('copy manually')
  expect(screen.getByRole('button', {name:'Copy excerpt'})).toBeEnabled()
  expect(screen.getByLabelText('Selected source excerpt')).toHaveValue('first\nsecond\n')
  fireEvent.change(screen.getByLabelText('Start line'), {target:{value:'2'}})
  await act(async () => finishOld())
  expect(screen.queryByText('Excerpt copied.')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Copy excerpt'}))
  await act(async () => {})
  expect(writeText).toHaveBeenLastCalledWith('second\n')
  expect(screen.getByRole('status')).toHaveTextContent('Excerpt copied.')
  expect(vi.getTimerCount()).toBe(0)
})
