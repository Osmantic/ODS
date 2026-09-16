import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelFileChanges from './PixelFileChanges'

const change = text => ({path:'main.js', change:'modified', additions:1, deletions:0,
  diff:[{type:'add', oldLine:null, newLine:1, text}]})
const copyButton = () => screen.getByRole('button', {name:'Copy changes to main.js'})
async function open() {
  fireEvent.click(screen.getByText('Edited main.js').closest('summary'))
  await screen.findByRole('region', {name:'Changes to main.js'})
}
afterEach(() => {vi.unstubAllGlobals(); vi.useRealTimers()})

it('allows only one clipboard write and rejects its receipt after the diff changes', async () => {
  let finish
  const writeText = vi.fn(() => new Promise(resolve => {finish = resolve}))
  vi.stubGlobal('navigator', {clipboard:{writeText}})
  const {rerender} = render(<PixelFileChanges changes={[change('old')]}/> )
  await open()
  fireEvent.click(copyButton()); fireEvent.click(copyButton())
  expect(writeText).toHaveBeenCalledTimes(1)
  expect(copyButton()).toBeDisabled()
  rerender(<PixelFileChanges changes={[change('new')]}/> )
  expect(copyButton()).toBeEnabled()
  await act(async () => finish())
  expect(copyButton()).toHaveTextContent('Copy')
  expect(copyButton()).not.toHaveTextContent('Copied')
  writeText.mockResolvedValue(undefined)
  fireEvent.click(copyButton())
  await waitFor(() => expect(copyButton()).toHaveTextContent('Copied'))
  expect(writeText).toHaveBeenLastCalledWith('+new')
})

it('ends a stalled clipboard write with a recoverable error and ignores its late success', async () => {
  let finish
  const writeText = vi.fn(() => new Promise(resolve => {finish = resolve}))
  vi.stubGlobal('navigator', {clipboard:{writeText}})
  render(<PixelFileChanges changes={[change('content')]}/> )
  await open()
  vi.useFakeTimers()
  fireEvent.click(copyButton())
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('alert')).toHaveTextContent('Clipboard access failed')
  expect(copyButton()).toBeEnabled()
  await act(async () => finish())
  expect(copyButton()).toHaveTextContent('Copy failed')
  writeText.mockResolvedValue(undefined)
  fireEvent.click(copyButton())
  await act(async () => {})
  expect(copyButton()).toHaveTextContent('Copied')
  expect(screen.queryByRole('alert')).toBeNull()
})
