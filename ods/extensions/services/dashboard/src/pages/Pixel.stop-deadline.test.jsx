import { act, fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import Pixel from './Pixel'

const json = value => ({ok:true,json:async()=>value})
let streamSignal
let cancelSignal
let resolveCancel

beforeEach(() => {
  localStorage.clear()
  streamSignal = cancelSignal = resolveCancel = null
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function transport(phase) {
  let attempts = 0
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (url === '/api/pixel/status') return json({available:true,model:'pixel/default'})
    if (url === '/api/pixel/chat/stream') {
      streamSignal = options.signal
      return {ok:true,headers:new Map([['content-type','text/event-stream']]),
        body:{getReader:()=>({read:()=>new Promise(()=>{}),releaseLock:()=>{}})}}
    }
    if (url === '/api/pixel/chat/cancel') {
      attempts++
      if (attempts > 1) return json({aborted:true})
      cancelSignal = options.signal
      const waiting = new Promise((resolve, reject) => {
        resolveCancel = resolve
        options.signal?.addEventListener('abort', () => reject(new globalThis.DOMException('Aborted','AbortError')), {once:true})
      })
      return phase === 'headers' ? waiting : {ok:true,json:()=>waiting}
    }
    throw new Error('Unexpected request: '+url)
  }))
}

async function start() {
  const mounted = render(<Pixel/>)
  await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Assistant...'), {target:{value:'Keep working until I stop'}})
  fireEvent.click(screen.getByTitle('Send'))
  await screen.findByTitle('Stop')
  return mounted
}

it.each(['headers','body'])('makes Stop retryable after a stalled %s without disconnecting the live task', async phase => {
  transport(phase)
  await start()
  vi.useFakeTimers()
  fireEvent.click(screen.getByTitle('Stop'))
  await act(async()=>{})
  expect(screen.getByTitle('Stopping')).toBeDisabled()
  await act(async()=>{await vi.advanceTimersByTimeAsync(15000)})
  expect(screen.getByText('Stop was not confirmed. The assistant is still connected; retry Stop.')).toBeInTheDocument()
  expect(cancelSignal.aborted).toBe(true)
  expect(streamSignal.aborted).toBe(false)
  expect(screen.queryByText('Response stopped')).toBeNull()
  expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).inFlight).toBe(true)
  await act(async()=>{resolveCancel({aborted:true})})
  expect(screen.queryByText('Response stopped')).toBeNull()
  fireEvent.click(screen.getByTitle('Stop'))
  await act(async()=>{})
  expect(screen.getByText('Response stopped')).toBeInTheDocument()
  expect(streamSignal.aborted).toBe(true)
  expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/cancel')).toHaveLength(2)
})

it('aborts a pending Stop request when the chat unmounts', async () => {
  transport('body')
  const {unmount} = await start()
  fireEvent.click(screen.getByTitle('Stop'))
  await act(async()=>{})
  unmount()
  expect(cancelSignal?.aborted).toBe(true)
})
