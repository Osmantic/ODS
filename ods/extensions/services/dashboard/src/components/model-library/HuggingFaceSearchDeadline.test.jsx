import {act, cleanup, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser' // eslint-disable-line no-unused-vars

const tick = ms => act(async () => {await vi.advanceTimersByTimeAsync(ms)})
const success = () => ({ok:true, json:async () => ({models:[], authenticated:false})})
beforeEach(() => vi.useFakeTimers())
afterEach(() => {cleanup(); vi.useRealTimers(); vi.unstubAllGlobals()})

it.each(['headers', 'body'])('releases a search stalled at %s and allows an explicit retry', async stage => {
  const pending = new Promise(() => {})
  const fetchMock = vi.fn().mockImplementationOnce(() => stage === 'headers'
    ? pending : Promise.resolve({ok:true, json:() => pending}))
    .mockResolvedValue(success())
  vi.stubGlobal('fetch', fetchMock)
  render(<HuggingFaceModelBrowser />)
  await tick(350)
  const signal = fetchMock.mock.calls[0][1].signal
  await tick(30000)
  expect(signal.aborted).toBe(true)
  expect(screen.getByRole('alert')).toHaveTextContent('search timed out')
  expect(screen.queryByText('No GGUF repositories found')).toBeNull()
  const retry = screen.getByRole('button', {name:'Retry search'})
  expect(retry).toBeEnabled()
  fireEvent.click(retry)
  await tick(350)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('No GGUF repositories found')).toBeVisible()
})

it('gives a replacement query its own deadline and cancels the old timer', async () => {
  const fetchMock = vi.fn(() => new Promise(() => {}))
  vi.stubGlobal('fetch', fetchMock)
  render(<HuggingFaceModelBrowser />)
  await tick(350)
  await tick(10000)
  fireEvent.change(screen.getByPlaceholderText('Search repositories, authors, or model families...'),
    {target:{value:'qwen'}})
  await tick(350)
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  const latest = fetchMock.mock.calls[1][1].signal
  await tick(20000)
  expect(latest.aborted).toBe(false)
  expect(screen.queryByRole('alert')).toBeNull()
  await tick(10000)
  expect(latest.aborted).toBe(true)
  expect(screen.getByRole('alert')).toHaveTextContent('search timed out')
})

it('does not time out or repeat a completed search', async () => {
  const fetchMock = vi.fn().mockResolvedValue(success())
  vi.stubGlobal('fetch', fetchMock)
  render(<HuggingFaceModelBrowser />)
  await tick(350)
  await tick(60000)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('No GGUF repositories found')).toBeVisible()
})
