import {StrictMode} from 'react'
import {act, cleanup, fireEvent, render, screen} from '@testing-library/react'
import ServiceMap from './ServiceMap'

const status = name => ({services:[{id:'ape',name,status:'healthy',port:7890}]})
const response = name => ({ok:true,json:async () => status(name)})
const settle = async () => {await act(async () => {})}

beforeEach(() => {
  vi.useFakeTimers({toFake:['setTimeout','clearTimeout','setInterval','clearInterval']})
})
afterEach(() => {cleanup();vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()})

it.each(['headers','body'])('releases stalled %s, allows retry, and ignores expired data', async phase => {
  let resolveOld
  const pending = new Promise(resolve => {resolveOld = resolve})
  const fetch = vi.fn()
    .mockResolvedValueOnce(phase === 'headers' ? pending : {ok:true,json:() => pending})
    .mockResolvedValue(response('Recovered service'))
  vi.stubGlobal('fetch', fetch)
  render(<ServiceMap compact />)
  await settle()
  expect(screen.getByRole('status')).toHaveTextContent('Loading integrations')
  await act(async () => {await vi.advanceTimersByTimeAsync(10000)})
  expect(fetch).toHaveBeenCalledTimes(1)
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('alert')).toHaveTextContent('timed out')
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  fireEvent.click(screen.getByRole('button',{name:'Retry'}))
  await settle()
  expect(screen.getByRole('button',{name:/Recovered service/})).toBeVisible()
  await act(async () => {resolveOld(phase === 'headers' ? response('Expired service') : status('Expired service'))})
  expect(screen.queryByRole('button',{name:/Expired service/})).toBeNull()
  expect(screen.getByRole('button',{name:/Recovered service/})).toBeVisible()
})

it('retains a loaded map while a refresh expires and recovers on the next poll', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response('Saved service'))
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue(response('Fresh service'))
  vi.stubGlobal('fetch',fetch)
  render(<ServiceMap compact />)
  await settle()
  await act(async () => {await vi.advanceTimersByTimeAsync(25000)})
  expect(screen.getByRole('alert')).toHaveTextContent('timed out')
  expect(screen.getByRole('button',{name:/Saved service/})).toBeVisible()
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(screen.getByRole('button',{name:/Fresh service/})).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(fetch).toHaveBeenCalledTimes(3)
})

it('gives the replacement StrictMode effect its own request', async () => {
  let resolveOld
  const fetch = vi.fn().mockImplementationOnce(() => new Promise(resolve => {resolveOld = resolve}))
    .mockResolvedValue(response('Current lifetime'))
  vi.stubGlobal('fetch',fetch)
  render(<StrictMode><ServiceMap compact /></StrictMode>)
  await settle()
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  expect(screen.getByRole('button',{name:/Current lifetime/})).toBeVisible()
  await act(async () => {resolveOld(response('Disposed lifetime'))})
  expect(screen.queryByRole('button',{name:/Disposed lifetime/})).toBeNull()
})

it('aborts pending work and removes all timers on unmount', async () => {
  const fetch = vi.fn(() => new Promise(() => {}))
  vi.stubGlobal('fetch',fetch)
  const mounted = render(<ServiceMap />)
  await settle()
  mounted.unmount()
  await settle()
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => {await vi.advanceTimersByTimeAsync(60000)})
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('pauses hidden polls and refreshes when visible without overlapping a request', async () => {
  let hidden = false
  vi.spyOn(document,'hidden','get').mockImplementation(() => hidden)
  const fetch = vi.fn().mockResolvedValue(response('Current service'))
  vi.stubGlobal('fetch',fetch)
  render(<ServiceMap />)
  await settle()
  hidden = true
  await act(async () => {await vi.advanceTimersByTimeAsync(30000)})
  expect(fetch).toHaveBeenCalledTimes(1)
  hidden = false
  fireEvent(document,new Event('visibilitychange'))
  fireEvent(document,new Event('visibilitychange'))
  await settle()
  expect(fetch).toHaveBeenCalledTimes(2)
})
