import {act, fireEvent, render, screen} from '@testing-library/react'
import PortalResponseActions from './PortalResponseActions'

const button = () => screen.getByRole('button', {name:'Copy response'})
const clipboard = writeText => vi.stubGlobal('navigator', {clipboard:{writeText}})
afterEach(() => {vi.unstubAllGlobals(); vi.useRealTimers()})

it('copies only the supplied Markdown and acknowledges success accessibly', async () => {
  vi.useFakeTimers()
  const content = '# Olá\n\n[Source](https://example.com)\n```js\nconst x = 1\n```\n'
  const writeText = vi.fn().mockResolvedValue(undefined)
  clipboard(writeText)
  render(<PortalResponseActions content={content}/>)
  fireEvent.click(button())
  await act(async () => {})
  expect(writeText).toHaveBeenCalledExactlyOnceWith(content)
  expect(button()).toHaveAttribute('title','Copied')
  expect(screen.getByRole('status')).toHaveTextContent('Response copied')
  expect(button()).toBeEnabled()
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(button()).toHaveAttribute('title','Copy response')
})

it('does not render actions for empty or invalid content', () => {
  const {rerender} = render(<PortalResponseActions content={'  \n  '}/>)
  expect(screen.queryByRole('button')).toBeNull()
  rerender(<PortalResponseActions content={null}/>)
  expect(screen.queryByRole('group')).toBeNull()
})

it('lets clipboard failures be retried without showing a success receipt', async () => {
  const writeText = vi.fn().mockRejectedValueOnce(new Error('Denied')).mockResolvedValue(undefined)
  clipboard(writeText)
  render(<PortalResponseActions content="Answer"/>)
  fireEvent.click(button())
  await act(async () => {})
  expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t copy')
  expect(button()).toBeEnabled()
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
  fireEvent.click(button())
  await act(async () => {})
  expect(button()).toHaveAttribute('title','Copied')
  expect(screen.queryByRole('alert')).toBeNull()
  expect(writeText).toHaveBeenCalledTimes(2)
})

it('handles unsupported clipboard access without removing the response action', () => {
  vi.stubGlobal('navigator', {})
  render(<PortalResponseActions content="Answer"/>)
  fireEvent.click(button())
  expect(screen.getByRole('alert')).toHaveTextContent('copy it manually')
  expect(button()).toBeEnabled()
})

it('bounds pending requests, ignores a late success, and permits retry', async () => {
  vi.useFakeTimers()
  let finish
  const writeText = vi.fn(() => new Promise(resolve => {finish = resolve}))
  clipboard(writeText)
  render(<PortalResponseActions content="Answer"/>)
  fireEvent.click(button()); fireEvent.click(button())
  expect(writeText).toHaveBeenCalledTimes(1)
  expect(button()).toBeDisabled()
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(button()).toBeEnabled()
  expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t copy')
  await act(async () => finish())
  expect(button()).toHaveAttribute('title','Try copying again')
  writeText.mockResolvedValue(undefined)
  fireEvent.click(button())
  await act(async () => {})
  expect(button()).toHaveAttribute('title','Copied')
})

it('never transfers pending clipboard feedback to different response content', async () => {
  let finish
  const writeText = vi.fn(() => new Promise(resolve => {finish = resolve}))
  clipboard(writeText)
  const {rerender} = render(<PortalResponseActions content="Old answer"/>)
  fireEvent.click(button())
  rerender(<PortalResponseActions content="New answer"/>)
  expect(button()).toBeEnabled()
  await act(async () => finish())
  expect(button()).toHaveAttribute('title','Copy response')
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
  writeText.mockResolvedValue(undefined)
  fireEvent.click(button())
  await act(async () => {})
  expect(writeText).toHaveBeenLastCalledWith('New answer')
  expect(button()).toHaveAttribute('title','Copied')
})

it('clears timers on unmount and ignores the pending promise afterward', async () => {
  vi.useFakeTimers()
  let finish
  clipboard(() => new Promise(resolve => {finish = resolve}))
  const {unmount} = render(<PortalResponseActions content="Answer"/>)
  fireEvent.click(button())
  expect(vi.getTimerCount()).toBe(1)
  unmount()
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => finish())
  expect(vi.getTimerCount()).toBe(0)
})
